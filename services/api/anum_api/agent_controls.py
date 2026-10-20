"""Owner controls over every agent: the emergency stop and per-agent monthly cost caps.

See docs/agent-controls.md. Two controls sit on top of the tool policy, autonomy and the
per-tenant and per-workspace model budgets (``model_budget.py``):

**Emergency stop** (one per tenant). While it is on:

* every model call is refused before it reaches the provider (``check_model_budget`` calls
  :func:`require_agents_running`, so task runs, chat, voice, retrieval, automations and the
  durable worker all stop at their next model call);
* every tool call is refused at :meth:`ToolRegistry.execute`, whichever path reached it, so
  nothing leaves ANUM through a connector (email, WhatsApp, marketing publish, Brevo, calls,
  delivery);
* approvals of held actions are refused (``POST /approvals/{id}/approve`` answers 423), so a
  pending send stays pending instead of going out;
* scheduled automations advance without firing (``emergency_stop`` outcome) and event or
  webhook triggers do not start runs;
* the Email Center scheduled sender and the marketing publisher hold due work: discovery
  skips a stopped tenant and a pass that still sees one leaves the item untouched;
* turning it on cancels the running and queued tasks of every workspace the owner belongs
  to. An inline run notices at its next step (``inline_runs.py``: claim, apply, settle each
  re-check the task) and never holds a transaction or lock while the model works.

Any workspace owner or the organization administrator may turn it on; only the organization
administrator (``organization_policy:manage``) may turn it off. Both are audited with the
actor and the reason (``agent_controls.emergency_stop.engaged`` / ``.released``).

**Per-agent caps**. A workspace owner sets a monthly estimated-cost limit (USD) per agent
profile. Model calls made while planning a run as that agent are attributed to it
(``BudgetedModelGateway.for_agent``); once its usage this UTC month reaches the cap, its
calls are refused with :class:`AgentCapExceededError` (HTTP 402, like the workspace budget).
The workspace and organization budgets still apply on top.

``ANUM_REPOSITORY_BACKEND=memory`` keeps everything in process memory; with ``postgresql``
it lives in ``agent_emergency_stops``, ``agent_cost_caps`` and ``agent_usage_monthly``
(migration ``0093_agent_controls``, forced RLS).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import dependencies as deps
from .audit import AuditRecord
from .authorization import Permission
from .model_budget import (
    MAX_COST_LIMIT_USD,
    MAX_TOKEN_LIMIT,
    BudgetKind,
    BudgetScope,
    ModelBudgetExceededError,
    ModelUsage,
    ModelUsageTotals,
    UsageDelta,
    month_start,
    next_month_start,
    utc_now,
)
from .schemas import TaskStatus, TenantContext, new_id
from .scoped_store import open_scoped_store

logger = logging.getLogger("anum.agent_controls")

STOP_MESSAGE = (
    "The emergency stop is on for this organization: agents, automations and outbound sends are paused. "
    "The organization owner can turn it off under Agent controls."
)
STOP_TOOL_SUMMARY = "Not run: " + STOP_MESSAGE
ENGAGE_DENIED = "Only a workspace owner or the organization administrator can turn on the emergency stop."
RELEASE_DENIED = (
    "Only the organization owner (the organization administrator role) can turn the emergency stop off."
)
CAPS_DENIED = "Only a workspace owner or the organization administrator can see agent cost caps."
CAP_SET_DENIED = "Only a workspace owner can change an agent's monthly cost cap."
HELD_DENIED = "Only a workspace owner or the organization administrator can see what the emergency stop holds."
CANCEL_LIMIT = 200
# Every model call that no custom agent makes (chat with the Assistant, voice, retrieval,
# runs without an agent) is attributed to the default assistant, so it can be capped too.
DEFAULT_ASSISTANT_ID = "assistant"
DEFAULT_ASSISTANT_NAME = "ANUM assistant"


# --------------------------------------------------------------------------- models


class EmergencyStop(BaseModel):
    engaged: bool = False
    reason: str | None = None
    changed_by: str | None = None
    changed_at: datetime | None = None
    engaged_at: datetime | None = None
    version: int = 0


class AgentCap(BaseModel):
    agent_id: str
    monthly_cost_limit_usd: float | None = None
    monthly_token_limit: int | None = None
    updated_by: str
    updated_at: datetime


class AgentsStoppedError(ModelBudgetExceededError):
    """The tenant's emergency stop is on: no model call, tool call or held send may start.

    A subclass of the budget refusal so every model-call path that already answers a budget
    refusal (402 in the API, a sentence in voice, a failed run on the worker, a failed
    automation step) handles it too; the API answers it with 423 and code ``agents_stopped``.
    """

    code = "agents_stopped"

    def __init__(self) -> None:
        self.scope = BudgetScope.TENANT
        self.kind = BudgetKind.COST
        self.resets_on = None  # type: ignore[assignment]
        Exception.__init__(self, STOP_MESSAGE)

    def spoken(self, arabic: bool) -> str:
        if arabic:
            return "أوقف المالك جميع الوكلاء مؤقتا بزر الإيقاف الطارئ، فلا أستطيع الإجابة الآن."
        return "An owner has turned on the emergency stop, so agents are paused and I can't answer right now."


def agent_cap_message(agent_name: str | None, limit: float, resets_on: date, kind: BudgetKind = BudgetKind.COST) -> str:
    who = f"The agent {agent_name}" if agent_name else "This agent"
    what = f"monthly cost cap (USD {limit:,.2f})" if kind == BudgetKind.COST else f"monthly token cap ({int(limit):,} tokens)"
    return (
        f"{who} has used its {what}. It resets on {resets_on.isoformat()} (UTC); "
        "a workspace owner can raise it under Agent controls."
    )


class AgentCapExceededError(ModelBudgetExceededError):
    """An agent profile has used its monthly cost cap (HTTP 402, code model_budget_exceeded)."""

    def __init__(
        self, agent_id: str, limit: float, resets_on: date, agent_name: str | None = None, kind: BudgetKind = BudgetKind.COST
    ) -> None:
        self.scope = BudgetScope.WORKSPACE
        self.kind = kind
        self.resets_on = resets_on
        self.agent_id = agent_id
        self.limit = limit
        Exception.__init__(self, agent_cap_message(agent_name, limit, resets_on, kind))

    def spoken(self, arabic: bool) -> str:
        if arabic:
            return "وصل هذا الوكيل إلى حد التكلفة الشهري، ويمكن لمالك مساحة العمل رفعه من إعدادات الوكلاء."
        return "This agent has reached its monthly cost cap. A workspace owner can raise it under Agent controls."


# --------------------------------------------------------------------------- stores


class AgentControlsStore(Protocol):
    def get_stop(self, context: TenantContext) -> EmergencyStop | None: ...
    def get_stop_for_update(self, context: TenantContext) -> EmergencyStop | None: ...
    def save_stop(self, context: TenantContext, stop: EmergencyStop) -> EmergencyStop: ...
    def list_caps(self, context: TenantContext) -> list[AgentCap]: ...
    def get_cap(self, context: TenantContext, agent_id: str) -> AgentCap | None: ...
    def set_cap(
        self, context: TenantContext, agent_id: str, cost: float | None, tokens: int | None, now: datetime
    ) -> AgentCap | None: ...
    def agent_usage(self, context: TenantContext, agent_id: str, month: date) -> ModelUsageTotals: ...
    def usage_by_agent(self, context: TenantContext, month: date) -> dict[str, ModelUsageTotals]: ...
    def record_agent_usage(
        self, context: TenantContext, agent_id: str, month: date, delta: UsageDelta, now: datetime
    ) -> ModelUsageTotals: ...


class InMemoryAgentControlsStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._stops: dict[str, EmergencyStop] = {}
        self._caps: dict[tuple[str, str, str], AgentCap] = {}
        self._usage: dict[tuple[str, str, str, date], ModelUsageTotals] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._stops.clear()
            self._caps.clear()
            self._usage.clear()

    def get_stop(self, context: TenantContext) -> EmergencyStop | None:
        with self._lock:
            found = self._stops.get(context.tenant_id)
            return found.model_copy() if found else None

    def get_stop_for_update(self, context: TenantContext) -> EmergencyStop | None:
        return self.get_stop(context)

    def save_stop(self, context: TenantContext, stop: EmergencyStop) -> EmergencyStop:
        with self._lock:
            self._stops[context.tenant_id] = stop.model_copy()
        return stop

    def list_caps(self, context: TenantContext) -> list[AgentCap]:
        with self._lock:
            return [
                cap.model_copy()
                for (tenant_id, workspace_id, _), cap in self._caps.items()
                if (tenant_id, workspace_id) == (context.tenant_id, context.workspace_id)
            ]

    def get_cap(self, context: TenantContext, agent_id: str) -> AgentCap | None:
        with self._lock:
            found = self._caps.get((context.tenant_id, context.workspace_id, agent_id))
            return found.model_copy() if found else None

    def set_cap(
        self, context: TenantContext, agent_id: str, cost: float | None, tokens: int | None, now: datetime
    ) -> AgentCap | None:
        key = (context.tenant_id, context.workspace_id, agent_id)
        with self._lock:
            if cost is None and tokens is None:
                self._caps.pop(key, None)
                return None
            cap = AgentCap(
                agent_id=agent_id, monthly_cost_limit_usd=cost, monthly_token_limit=tokens, updated_by=context.user_id, updated_at=now
            )
            self._caps[key] = cap
            return cap.model_copy()

    def agent_usage(self, context: TenantContext, agent_id: str, month: date) -> ModelUsageTotals:
        with self._lock:
            return self._usage.get((context.tenant_id, context.workspace_id, agent_id, month), ModelUsageTotals()).model_copy()

    def usage_by_agent(self, context: TenantContext, month: date) -> dict[str, ModelUsageTotals]:
        with self._lock:
            return {
                agent_id: totals.model_copy()
                for (tenant_id, workspace_id, agent_id, row_month), totals in self._usage.items()
                if (tenant_id, workspace_id, row_month) == (context.tenant_id, context.workspace_id, month)
            }

    def record_agent_usage(
        self, context: TenantContext, agent_id: str, month: date, delta: UsageDelta, now: datetime
    ) -> ModelUsageTotals:
        key = (context.tenant_id, context.workspace_id, agent_id, month)
        with self._lock:
            current = self._usage.get(key, ModelUsageTotals())
            updated = ModelUsageTotals(
                input_tokens=current.input_tokens + delta.input_tokens,
                output_tokens=current.output_tokens + delta.output_tokens,
                estimated_cost_usd=current.estimated_cost_usd + (delta.estimated_cost_usd or 0.0),
                calls=current.calls + 1,
                unpriced_calls=current.unpriced_calls + (delta.estimated_cost_usd is None),
            )
            self._usage[key] = updated
            return updated.model_copy()


memory_agent_controls_store = InMemoryAgentControlsStore()


def _sql_store(session):  # type: ignore[no-untyped-def]
    from .db.agent_controls_repository import SqlAlchemyAgentControlsStore

    return SqlAlchemyAgentControlsStore(session)


@contextmanager
def open_agent_controls_store(context: TenantContext) -> Iterator[AgentControlsStore]:
    """The controls for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""
    with open_scoped_store(context, memory_agent_controls_store, _sql_store) as store:
        yield store


def store_in(repository: Any) -> AgentControlsStore:
    """The controls inside an existing unit of work (``_unit_of_work``), so a change and its
    audit record commit together."""
    from .settings import settings

    if settings.repository_backend == "memory":
        return memory_agent_controls_store
    return _sql_store(repository.session)


# --------------------------------------------------------------------------- enforcement


def emergency_stop(context: TenantContext) -> EmergencyStop:
    with open_agent_controls_store(context) as store:
        return store.get_stop(context) or EmergencyStop()


def agents_stopped(context: TenantContext) -> bool:
    """Whether the tenant's emergency stop is on. A blocking read: call it off the event loop."""
    return emergency_stop(context).engaged


def require_agents_running(context: TenantContext) -> None:
    """Raise :class:`AgentsStoppedError` while the tenant's emergency stop is on."""
    if agents_stopped(context):
        logger.warning(
            "agents_stopped_refusal tenant_id=%s",
            _log_safe(context.tenant_id),
            extra={"anum_agent_controls": {"event": "refused", "tenant_id": _log_safe(context.tenant_id)}},
        )
        raise AgentsStoppedError()


def check_agent_cap(context: TenantContext, agent_id: str, *, now: datetime | None = None) -> None:
    """Raise :class:`AgentCapExceededError` when ``agent_id`` used its cap this month."""
    month = month_start(now or utc_now())
    with open_agent_controls_store(context) as store:
        cap = store.get_cap(context, agent_id)
        if cap is None:
            return
        used = store.agent_usage(context, agent_id, month)
    kind: BudgetKind | None = None
    limit: float = 0.0
    if cap.monthly_cost_limit_usd is not None and used.estimated_cost_usd >= cap.monthly_cost_limit_usd:
        kind, limit = BudgetKind.COST, cap.monthly_cost_limit_usd
    elif cap.monthly_token_limit is not None and used.total_tokens >= cap.monthly_token_limit:
        kind, limit = BudgetKind.TOKENS, float(cap.monthly_token_limit)
    if kind is not None:
        logger.warning(
            "agent_cap_exceeded tenant_id=%s workspace_id=%s agent_id=%s",
            _log_safe(context.tenant_id),
            _log_safe(context.workspace_id),
            _log_safe(agent_id),
            extra={"anum_agent_controls": {"event": "cap_exceeded", "agent_id": _log_safe(agent_id)}},
        )
        raise AgentCapExceededError(agent_id, limit, next_month_start(month), _agent_name(context, agent_id), kind)


def record_agent_usage(context: TenantContext, agent_id: str, usage: ModelUsage, *, now: datetime | None = None) -> ModelUsageTotals:
    moment = now or utc_now()
    with open_agent_controls_store(context) as store:
        return store.record_agent_usage(context, agent_id, month_start(moment), UsageDelta.of(usage), moment)


def _agent_name(context: TenantContext, agent_id: str) -> str | None:
    if agent_id == DEFAULT_ASSISTANT_ID:
        return DEFAULT_ASSISTANT_NAME
    try:
        from .agent_profiles import load_agent

        profile = load_agent(context, agent_id)
    except Exception:  # noqa: BLE001 - the refusal must not fail on the name
        return None
    return profile.name if profile is not None else None


def _log_safe(value: str) -> str:
    return value.replace("\r", "").replace("\n", "")


# Told about every task the stop cancelled (main.py: the durable dispatcher's cancel signal).
CancelNotifier = Callable[[TenantContext, str], Awaitable[None]]
_cancel_notifier: CancelNotifier | None = None


def configure_cancel_notifier(notifier: CancelNotifier | None) -> None:
    global _cancel_notifier
    _cancel_notifier = notifier


def cancel_in_flight(context: TenantContext, workspace_ids: list[str]) -> list[tuple[TenantContext, str]]:
    """Cancel the running and queued tasks of ``workspace_ids`` (each in its own short unit of
    work). Returns ``(workspace context, task id)`` for every task cancelled.

    No lock is held while a run works (``inline_runs.py``), so the brief row lock taken here
    never waits on a model call; the run sees the cancel at its next step and stops.
    """
    from .dependencies import _unit_of_work
    from .events import CanonicalEventName, create_event
    from .schemas import ApprovalStatus, RunPhase
    from .tasks import TaskFilters

    cancelled: list[tuple[TenantContext, str]] = []
    for workspace_id in workspace_ids:
        scoped = TenantContext(
            tenant_id=context.tenant_id, workspace_id=workspace_id, user_id=context.user_id, roles=list(context.roles)
        )
        try:
            with _unit_of_work(scoped, require_membership=False) as repository:
                tasks = repository.list_tasks(
                    scoped,
                    limit=CANCEL_LIMIT,
                    filters=TaskFilters(statuses=frozenset({TaskStatus.RUNNING, TaskStatus.QUEUED})),
                )
                for listed in tasks:
                    task = repository.get_task_for_update(listed.id, scoped)
                    if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.QUEUED}:
                        continue
                    now = utc_now()
                    task.status = TaskStatus.CANCELLED
                    task.updated_at = now
                    repository.save_task(task)
                    run = repository.find_run_for_task(task.id, scoped)
                    if run and run.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                        run.status = TaskStatus.CANCELLED
                        run.checkpoint.phase = RunPhase.CANCELLED
                        run.checkpoint.version += 1
                        run.updated_at = now
                        repository.save_run(run)
                    for approval in repository.list_task_approvals(task.id, scoped):
                        if approval.status == ApprovalStatus.PENDING:
                            fresh = repository.get_approval_for_update(approval.id, scoped)
                            if fresh is not None and fresh.status == ApprovalStatus.PENDING:
                                fresh.status = ApprovalStatus.EXPIRED
                                fresh.decided_at = now
                                repository.save_approval(fresh)
                    repository.record_event(
                        create_event(
                            CanonicalEventName.TASK_CANCELLED, scoped, task.id, {"task_id": task.id, "reason": "emergency_stop"},
                            correlation_id=task.id,
                        ).event
                    )
                    cancelled.append((scoped, task.id))
        except Exception:  # noqa: BLE001 - one workspace must not stop the others; runs are refused anyway
            logger.exception(
                "agent_controls_cancel_failed",
                extra={"anum_agent_controls": {"event": "cancel_failed", "workspace_id": _log_safe(workspace_id)}},
            )
    return cancelled


# --------------------------------------------------------------------------- API

router = APIRouter(prefix="/api/v1/agent-controls", tags=["agent-controls"])


class EmergencyStopView(BaseModel):
    engaged: bool
    reason: str | None
    changed_by: str | None
    changed_by_you: bool
    changed_at: datetime | None
    engaged_at: datetime | None


class AgentControlsView(BaseModel):
    emergency_stop: EmergencyStopView
    can_engage: bool
    can_release: bool
    # Tasks cancelled by this change (only on the response that turned the stop on).
    cancelled_tasks: int = 0


class EmergencyStopChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engaged: bool
    reason: str = Field(min_length=3, max_length=300)

    @field_validator("reason")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 3:
            raise ValueError("Say why in at least 3 characters.")
        return cleaned


class AgentCapLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_cost_limit_usd: float | None = Field(default=None, ge=0, le=MAX_COST_LIMIT_USD)
    monthly_token_limit: int | None = Field(default=None, ge=0, le=MAX_TOKEN_LIMIT)


class AgentCapView(BaseModel):
    agent_id: str
    name: str
    status: str
    sharing: str
    # The default assistant: every model call no custom agent makes (chat, voice, retrieval).
    is_default: bool = False
    monthly_cost_limit_usd: float | None
    monthly_token_limit: int | None = None
    usage: ModelUsageTotals
    total_tokens: int = 0
    percent_used: float | None
    token_percent_used: float | None = None
    exceeded: bool
    updated_by: str | None
    updated_at: datetime | None


class AgentCapsOverview(BaseModel):
    period_start: date
    resets_on: date
    can_manage: bool
    total: int
    items: list[AgentCapView]


class HeldItem(BaseModel):
    kind: str  # approval, mail, post
    id: str
    title: str
    detail: str
    at: datetime | None
    by: str | None = None


class HeldPage(BaseModel):
    engaged: bool
    total: int
    limit: int
    offset: int
    items: list[HeldItem]


def _can_engage(context: TenantContext) -> bool:
    try:
        deps.require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, ENGAGE_DENIED)
    except HTTPException:
        return False
    return True


def _can_release(context: TenantContext) -> bool:
    return bool(deps.is_organization_admin(context))


def _view(context: TenantContext, stop: EmergencyStop, cancelled: int = 0) -> AgentControlsView:
    return AgentControlsView(
        emergency_stop=EmergencyStopView(
            engaged=stop.engaged,
            reason=stop.reason,
            changed_by=stop.changed_by,
            changed_by_you=stop.changed_by is not None and stop.changed_by == context.user_id,
            changed_at=stop.changed_at,
            engaged_at=stop.engaged_at,
        ),
        can_engage=_can_engage(context),
        can_release=_can_release(context),
        cancelled_tasks=cancelled,
    )


def _audit(repository: Any, context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome="success",
            correlation_id=new_id("correlation"),
            created_at=utc_now(),
            metadata=metadata,
        )
    )


NOTICE_KIND = "agent_controls"


def notice_text(stop: EmergencyStop) -> tuple[str, str]:
    """Title and body of the owners' notice: who changed it and why, nothing else."""
    if stop.engaged:
        return (
            "Emergency stop turned on",
            f"{stop.changed_by or 'An owner'} paused every agent, automation and outbound send. Reason: {stop.reason or '-'}",
        )
    return (
        "Emergency stop turned off",
        f"{stop.changed_by or 'The organization owner'} let agents run again. Reason: {stop.reason or '-'}",
    )


def notify_owners(context: TenantContext, stop: EmergencyStop, workspace_ids: list[str]) -> int:
    """An in-app notification (and through it the ``notification.created`` event and the phone
    push) for every other active owner of each workspace. Best effort: the change committed."""
    from .notifications import deliver_pending_email
    from .team_work import open_team_unit

    title, body = notice_text(stop)
    sent = 0
    for workspace_id in workspace_ids:
        scoped = TenantContext(
            tenant_id=context.tenant_id, workspace_id=workspace_id, user_id=context.user_id, roles=list(context.roles)
        )
        try:
            with open_team_unit(scoped) as unit:
                owners = [
                    member.user_id
                    for member in unit.repository.list_memberships(scoped)
                    if member.active and member.role.lower() == "owner" and member.user_id != context.user_id
                ]
                for user_id in owners:
                    unit.notify(user_id, NOTICE_KIND, title, body, None)
                    sent += 1
                pending = list(unit.pending_email)
            if pending:
                deliver_pending_email(scoped, pending)
        except Exception:  # noqa: BLE001 - never undo the stop over a notice
            logger.exception(
                "agent_controls_notice_failed",
                extra={"anum_agent_controls": {"event": "notice_failed", "workspace_id": _log_safe(workspace_id)}},
            )
    return sent


def _member_workspaces(context: TenantContext) -> list[str]:
    """The workspaces of this tenant the caller belongs to (always including this one)."""
    found = [context.workspace_id]
    try:
        with deps._unit_of_work(context, require_membership=False) as repository:
            memberships = repository.list_caller_memberships(context)
    except Exception:  # noqa: BLE001 - fall back to this workspace only
        return found
    for membership in memberships:
        if membership.tenant_id == context.tenant_id and membership.workspace_id not in found:
            found.append(membership.workspace_id)
    return found


@router.get("", response_model=AgentControlsView)
def get_agent_controls(context: TenantContext = Depends(deps.tenant_context)) -> AgentControlsView:
    """Whether the emergency stop is on, and what this caller may do about it (any member)."""
    deps.require_permission(context, Permission.TASK_READ)
    return _view(context, emergency_stop(context))

@router.put("/emergency-stop", response_model=AgentControlsView)
async def set_emergency_stop(
    payload: EmergencyStopChange, context: TenantContext = Depends(deps.tenant_context)
) -> AgentControlsView:
    """Turn the organization's emergency stop on (owners, the organization administrator)
    or off (the organization administrator only). Audited with the actor and reason."""
    deps.require_permission(context, Permission.TASK_READ)
    if payload.engaged:
        deps.require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, ENGAGE_DENIED)
    elif not _can_release(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=RELEASE_DENIED)

    def change() -> EmergencyStop:
        with deps._unit_of_work(context, require_membership=True) as repository:
            store = store_in(repository)
            current = store.get_stop_for_update(context) or EmergencyStop()
            if current.engaged == payload.engaged:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The emergency stop is already on." if payload.engaged else "The emergency stop is already off.",
                )
            now = utc_now()
            stop = EmergencyStop(
                engaged=payload.engaged,
                reason=payload.reason,
                changed_by=context.user_id,
                changed_at=now,
                engaged_at=now if payload.engaged else None,
                version=current.version + 1,
            )
            store.save_stop(context, stop)
            _audit(
                repository,
                context,
                "agent_controls.emergency_stop.engaged" if payload.engaged else "agent_controls.emergency_stop.released",
                f"tenant:{context.tenant_id}",
                {"engaged": payload.engaged, "reason": payload.reason, "version": stop.version},
            )
            return stop

    stop = await asyncio.to_thread(change)
    cancelled: list[tuple[TenantContext, str]] = []
    workspaces = await asyncio.to_thread(_member_workspaces, context)
    # Owners hear about it in the app and on their phones (the team-work push kind).
    await asyncio.to_thread(notify_owners, context, stop, workspaces)
    if stop.engaged:
        cancelled = await asyncio.to_thread(cancel_in_flight, context, workspaces)
        if _cancel_notifier is not None:
            for scoped, task_id in cancelled:
                try:
                    await _cancel_notifier(scoped, task_id)
                except Exception:  # noqa: BLE001 - the stored cancel already holds
                    logger.warning("agent_controls_cancel_signal_failed", extra={"anum_agent_controls": {"event": "signal_failed"}})
        if cancelled:

            def record_cancelled() -> None:
                with deps._unit_of_work(context, require_membership=False) as repository:
                    _audit(
                        repository, context, "agent_controls.emergency_stop.cancelled_tasks", f"tenant:{context.tenant_id}",
                        {"tasks": len(cancelled), "workspaces": len({scoped.workspace_id for scoped, _ in cancelled})},
                    )

            await asyncio.to_thread(record_cancelled)
    logger.warning(
        "agent_controls_emergency_stop engaged=%s tenant_id=%s",
        stop.engaged,
        _log_safe(context.tenant_id),
        extra={"anum_agent_controls": {"event": "engaged" if stop.engaged else "released", "cancelled": len(cancelled)}},
    )
    return _view(context, stop, len(cancelled))

def _percent(used: float, limit: float | None) -> float | None:
    if limit is None:
        return None
    return 100.0 if limit <= 0 else min(used / limit * 100, 1000.0)


def _cap_view(
    agent_id: str, name: str, status_value: str, sharing: str, cap: AgentCap | None, used: ModelUsageTotals
) -> AgentCapView:
    cost = cap.monthly_cost_limit_usd if cap else None
    tokens = cap.monthly_token_limit if cap else None
    exceeded = (cost is not None and used.estimated_cost_usd >= cost) or (tokens is not None and used.total_tokens >= tokens)
    return AgentCapView(
        agent_id=agent_id,
        name=name,
        status=status_value,
        sharing=sharing,
        is_default=agent_id == DEFAULT_ASSISTANT_ID,
        monthly_cost_limit_usd=cost,
        monthly_token_limit=tokens,
        usage=used,
        total_tokens=used.total_tokens,
        percent_used=_percent(used.estimated_cost_usd, cost),
        token_percent_used=_percent(float(used.total_tokens), None if tokens is None else float(tokens)),
        exceeded=exceeded,
        updated_by=cap.updated_by if cap else None,
        updated_at=cap.updated_at if cap else None,
    )


@router.get("/agent-caps", response_model=AgentCapsOverview)
def get_agent_caps(
    context: TenantContext = Depends(deps.tenant_context),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AgentCapsOverview:
    """The default assistant and every agent this owner can see, with caps and usage this UTC month."""
    deps.require_owner_or_org_admin(context, Permission.ORGANIZATION_MANAGE, CAPS_DENIED)
    from .agent_profiles import open_agent_store

    month = month_start(utc_now())
    with open_agent_store(context) as agents:
        profiles = agents.list(context)
    with open_agent_controls_store(context) as store:
        caps = {cap.agent_id: cap for cap in store.list_caps(context)}
        usage = store.usage_by_agent(context, month)
    needle = (q or "").strip().lower()
    rows: list[tuple[str, str, str, str]] = []
    if not needle or needle in DEFAULT_ASSISTANT_NAME.lower():
        rows.append((DEFAULT_ASSISTANT_ID, DEFAULT_ASSISTANT_NAME, "active", "workspace"))
    rows.extend(
        (p.id, p.name, p.status.value, p.sharing.value)
        for p in sorted(profiles, key=lambda p: (p.name.lower(), p.id))
        if not needle or needle in p.name.lower() or needle in (p.description or "").lower()
    )
    items = [
        _cap_view(agent_id, name, state, sharing, caps.get(agent_id), usage.get(agent_id, ModelUsageTotals()))
        for agent_id, name, state, sharing in rows[offset : offset + limit]
    ]
    return AgentCapsOverview(
        period_start=month,
        resets_on=next_month_start(month),
        can_manage=deps.has_permission(context, Permission.ORGANIZATION_MANAGE),
        total=len(rows),
        items=items,
    )


def _describe_cap(found: AgentCap | None) -> str:
    if found is None:
        return "none"
    cost = "none" if found.monthly_cost_limit_usd is None else found.monthly_cost_limit_usd
    count = "none" if found.monthly_token_limit is None else found.monthly_token_limit
    return f"cost_usd={cost} tokens={count}"


@router.put("/agent-caps/{agent_id}", response_model=AgentCapView)
def set_agent_cap(
    agent_id: str, payload: AgentCapLimit, context: TenantContext = Depends(deps.tenant_context)
) -> AgentCapView:
    """Set (or clear with nulls) one agent's monthly cost and token caps (workspace owners)."""
    if not deps.has_permission(context, Permission.ORGANIZATION_MANAGE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CAP_SET_DENIED)
    if agent_id == DEFAULT_ASSISTANT_ID:
        name, state, sharing = DEFAULT_ASSISTANT_NAME, "active", "workspace"
    else:
        from .agent_profiles import open_agent_store

        with open_agent_store(context) as agents:
            profile = agents.get(context, agent_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found in this workspace")
        name, state, sharing = profile.name, profile.status.value, profile.sharing.value
    now = utc_now()
    with deps._unit_of_work(context, require_membership=True) as repository:
        store = store_in(repository)
        previous = store.get_cap(context, agent_id)
        cap = store.set_cap(context, agent_id, payload.monthly_cost_limit_usd, payload.monthly_token_limit, now)
        used = store.agent_usage(context, agent_id, month_start(now))
        # Rendered as text: audit metadata redacts any key that mentions "token".
        _audit(
            repository, context, "agent_controls.agent_cap.update", f"agent:{agent_id}",
            {"previous_limits": _describe_cap(previous), "limits": _describe_cap(cap)},
        )
    return _cap_view(agent_id, name, state, sharing, cap, used)


HELD_SOURCE_LIMIT = 500


def _held_items(context: TenantContext, repository: Any) -> list[HeldItem]:
    """What the stop holds in this workspace: pending approvals, the caller's own scheduled
    mail (mailboxes are private to their owner) and scheduled social posts."""
    from .schemas import ApprovalStatus

    items: list[HeldItem] = []
    for approval in repository.list_approvals(context):
        if approval.status != ApprovalStatus.PENDING:
            continue
        items.append(HeldItem(
            kind="approval", id=approval.id, title=approval.action,
            detail=str(getattr(approval, "target", None) or approval.reason or "")[:200],
            at=approval.created_at, by=getattr(approval, "requested_by", None),
        ))
        if len(items) >= HELD_SOURCE_LIMIT:
            break
    try:
        from .mailbox.store import open_mail_store

        with open_mail_store(context) as mail:
            for draft in mail.list_scheduled(None, HELD_SOURCE_LIMIT):
                recipients = len(draft.to) + len(draft.cc) + len(draft.bcc)
                items.append(HeldItem(
                    kind="mail", id=draft.id, title=draft.subject or "(no subject)",
                    detail=f"{recipients} recipient{'s' if recipients != 1 else ''}", at=draft.send_at, by=draft.owner_user_id,
                ))
    except Exception:  # noqa: BLE001 - the Email Center may be off on this server
        logger.debug("agent_controls_held_mail_unavailable")
    try:
        from .marketing.models import PostStatus
        from .marketing.store import PostQuery, open_marketing_store

        with open_marketing_store(context) as marketing:
            for post in marketing.list_posts(context, PostQuery(statuses={PostStatus.SCHEDULED}, limit=HELD_SOURCE_LIMIT)):
                items.append(HeldItem(
                    kind="post", id=post.id, title=(post.title or post.text or "(no text)")[:120],
                    detail=post.platform.value, at=post.scheduled_at,
                ))
    except Exception:  # noqa: BLE001 - marketing may not be set up here
        logger.debug("agent_controls_held_posts_unavailable")
    far = datetime.max.replace(tzinfo=utc_now().tzinfo)
    return sorted(items, key=lambda item: (item.at or far, item.kind, item.id))


@router.get("/held", response_model=HeldPage)
def get_held(
    context: TenantContext = Depends(deps.tenant_context),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> HeldPage:
    """Sends and actions the stop holds in this workspace, oldest first, paged (owners)."""
    deps.require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, HELD_DENIED)
    with deps._unit_of_work(context, require_membership=False) as repository:
        items = _held_items(context, repository)
    return HeldPage(
        engaged=emergency_stop(context).engaged, total=len(items), limit=limit, offset=offset,
        items=items[offset : offset + limit],
    )
