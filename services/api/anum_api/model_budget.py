"""Per-tenant and per-workspace monthly model budgets (threat model G4).

An organization administrator sets the organization (tenant) budget and a workspace owner
sets each workspace's budget: an optional
estimated-cost limit in USD and an optional token limit per UTC calendar month. Every
model call made for a workspace (task planning and voice answers) goes through
:class:`BudgetedModelGateway`, which:

1. refuses the call (text generation and retrieval embeddings alike) before it reaches the provider when the tenant or the workspace has
   already used its budget for the current month (:class:`ModelBudgetExceededError`,
   answered as HTTP 402 by the API and as a spoken sentence by voice);
2. after a successful call adds the gateway's usage metadata (input/output tokens and
   ``estimated_cost_usd``) to the workspace's monthly usage row;
3. logs ``model_budget_threshold`` and counts ``anum.model.budget.thresholds`` when a
   call crosses 80% or 100% of a limit, and tells the workspace's owners in the app when
   it crosses 50, 75, 90 or 100 percent (``budget_alerts.py``).

The check does not reserve spend, so concurrent calls admitted just under a limit can
overshoot it by at most those calls' usage. Calls whose model has no price estimate
count tokens but no cost (``unpriced_calls``). Usage is stored per workspace and month;
tenant usage is the sum of its workspaces.

``ANUM_REPOSITORY_BACKEND=memory`` keeps budgets and usage in process memory; with
``postgresql`` they live in the RLS-protected ``model_budgets`` and
``model_usage_monthly`` tables (migration ``0009_model_budgets``).
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, repository_context, require_permission, tenant_context
from .model_gateway import (
    ChatMessage,
    EmbeddingResponse,
    ModelGateway,
    ModelResponse,
    ModelRoute,
    ModelUsage,
    StructuredModel,
    UsageCallback,
    VisionImage,
    route_gateway,
    estimate_tokens,
    vision_gateway,
)
from .repository import AnumRepository
from .schemas import TenantContext, new_id
from .scoped_store import open_scoped_store
from .settings import settings
from .telemetry import telemetry

logger = logging.getLogger("anum.model_budget")

THRESHOLDS = (0.8, 1.0)
MAX_COST_LIMIT_USD = 1_000_000_000.0
MAX_TOKEN_LIMIT = 10**15


class BudgetScope(StrEnum):
    TENANT = "tenant"
    WORKSPACE = "workspace"


class BudgetKind(StrEnum):
    COST = "cost"
    TOKENS = "tokens"


class UsageGroup(StrEnum):
    """How ``GET /model-budgets/usage`` groups the recorded spend."""

    DAY = "day"
    MONTH = "month"
    USER = "user"
    AGENT = "agent"
    FEATURE = "feature"


# The feature a model call is made for (docs/model-gateway.md#spend-breakdown): set by
# ``feature_scope`` or the gateway, read when the call's usage is recorded.
_FEATURE: ContextVar[str | None] = ContextVar("anum_model_usage_feature", default=None)
UNKNOWN_FEATURE = "other"
# Modules whose name does not say what they do, and features made of several modules.
_FEATURE_ALIASES = {
    "main": "tasks",
    "worker": "tasks",
    "inline_runs": "tasks",
    "durable_runs": "tasks",
    "runtime": "tasks",
    "executive_pa": "executive",
    "meeting_analysis": "meetings",
    "live_agent_meeting": "meetings",
    "email_inbox": "email",
    "mail": "email",
    "knowledge_base": "knowledge",
    "retrieval": "knowledge",
    "retrieval_api": "knowledge",
    "conversation_memory": "assistant",
    "conversations": "assistant",
    "speech_engines": "speech",
    "voice_call": "voice",
    "action_browser": "browser",
    "browser_research": "browser",
    "web_search": "browser",
}


def feature_for_module(module: str | None) -> str:
    """The feature name for the module that asked for a gateway (``anum_api.voice`` is ``voice``,
    ``anum_api.finance.sms_ai`` is ``finance``, ``anum_api.main`` is ``tasks``)."""
    if not module:
        return UNKNOWN_FEATURE
    parts = module.split(".")
    if parts[0] == "anum_api":
        parts = parts[1:]
    if not parts or not parts[0]:
        return UNKNOWN_FEATURE
    head = parts[0]
    name = _FEATURE_ALIASES.get(head, head)
    return name[:80]


def current_feature() -> str | None:
    return _FEATURE.get()


@contextmanager
def feature_scope(feature: str) -> Iterator[None]:
    """Attribute every model call recorded inside the block to ``feature``."""
    token = _FEATURE.set(feature[:80])
    try:
        yield
    finally:
        _FEATURE.reset(token)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def month_start(now: datetime) -> date:
    """First day of ``now``'s UTC calendar month: the key usage is stored under."""
    now = now.astimezone(timezone.utc)
    return date(now.year, now.month, 1)


def next_month_start(month: date) -> date:
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


class ModelBudgetLimits(BaseModel):
    monthly_cost_limit_usd: float | None = Field(default=None, ge=0, le=MAX_COST_LIMIT_USD)
    monthly_token_limit: int | None = Field(default=None, ge=0, le=MAX_TOKEN_LIMIT)
    # The owner's own alert threshold, a percent of either limit, told to the workspace owners in
    # the app like the fixed 50, 75, 90 and 100 percent notices (budget_alerts.py). None = only those.
    alert_percent: int | None = Field(default=None, ge=1, le=99)


class ModelBudget(ModelBudgetLimits):
    scope: BudgetScope
    updated_at: datetime
    updated_by: str

    def limit(self, kind: BudgetKind) -> float | None:
        return self.monthly_cost_limit_usd if kind == BudgetKind.COST else self.monthly_token_limit


class ModelUsageTotals(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def amount(self, kind: BudgetKind) -> float:
        return self.estimated_cost_usd if kind == BudgetKind.COST else float(self.total_tokens)

    def plus(self, delta: UsageDelta) -> ModelUsageTotals:
        return ModelUsageTotals(
            input_tokens=self.input_tokens + delta.input_tokens,
            output_tokens=self.output_tokens + delta.output_tokens,
            estimated_cost_usd=self.estimated_cost_usd + (delta.estimated_cost_usd or 0.0),
            calls=self.calls + 1,
            unpriced_calls=self.unpriced_calls + bool(delta.unpriced),
        )


@dataclass(frozen=True)
class UsageKey:
    """One recorded call's attribution: the UTC day, the caller, the agent and the feature."""

    day: date
    user_id: str
    agent_id: str
    feature: str


class UsageBreakdownRow(BaseModel):
    """One group of ``GET /model-budgets/usage``: a day (``YYYY-MM-DD``), a month (``YYYY-MM-01``),
    a user id, an agent id or a feature name, with its figures."""

    key: str
    # A name for the key when one is known (an agent's name); clients show the key otherwise.
    label: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0

    @classmethod
    def of(cls, key: str, totals: ModelUsageTotals, label: str | None = None) -> UsageBreakdownRow:
        return cls(key=key, label=label, total_tokens=totals.total_tokens, **totals.model_dump())

    def totals(self) -> ModelUsageTotals:
        return ModelUsageTotals(
            input_tokens=self.input_tokens, output_tokens=self.output_tokens, estimated_cost_usd=self.estimated_cost_usd,
            calls=self.calls, unpriced_calls=self.unpriced_calls,
        )


@dataclass(frozen=True)
class UsageDelta:
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    # The model has no price: counted in ``unpriced_calls`` even when a worst-case cost was
    # assumed for it (``with_assumed_cost``). Left out, it follows from a missing cost.
    unpriced: bool | None = None

    def __post_init__(self) -> None:
        if self.unpriced is None:
            object.__setattr__(self, "unpriced", self.estimated_cost_usd is None)

    @classmethod
    def of(cls, usage: ModelUsage) -> UsageDelta:
        return cls(usage.input_tokens, usage.output_tokens, usage.estimated_cost_usd)

    def with_assumed_cost(self, model: str) -> UsageDelta:
        """For a call no price is known for, under a cost limit: the cost at the configured
        worst-case price (``ANUM_MODEL_UNPRICED_PRICE``), so the limit still counts it. Cache
        discounts are not applied (worst case). Unchanged when priced or when switched off."""
        price = settings.model_unpriced_price
        if self.estimated_cost_usd is not None or price is None:
            return self
        cost = (self.input_tokens * price.input_per_million + self.output_tokens * price.output_per_million) / 1_000_000
        _warn_unpriced_once(model)
        return replace(self, estimated_cost_usd=round(cost, 8))

    def amount(self, kind: BudgetKind) -> float:
        if kind == BudgetKind.COST:
            return self.estimated_cost_usd or 0.0
        return float(self.input_tokens + self.output_tokens)


class ModelBudgetStore(Protocol):
    def get_budget(self, context: TenantContext, scope: BudgetScope) -> ModelBudget | None: ...

    def set_budget(
        self, context: TenantContext, scope: BudgetScope, limits: ModelBudgetLimits, now: datetime
    ) -> ModelBudget: ...

    def usage(self, context: TenantContext, scope: BudgetScope, month: date) -> ModelUsageTotals: ...

    def record_usage(
        self, context: TenantContext, month: date, delta: UsageDelta, now: datetime
    ) -> ModelUsageTotals:
        """Atomically add ``delta`` to the workspace's month; return the workspace total."""
        ...

    def record_usage_detail(self, context: TenantContext, key: UsageKey, delta: UsageDelta, now: datetime) -> None:
        """Add the same call to the workspace's day, caller, agent and feature row."""
        ...

    def usage_breakdown(
        self, context: TenantContext, scope: BudgetScope, group_by: UsageGroup, start: date, end: date
    ) -> list[UsageBreakdownRow]:
        """The recorded spend between ``start`` (inclusive) and ``end`` (exclusive), one row per
        group, in no particular order. ``month`` reads the monthly rows (exact for every month);
        the other groups read the daily rows (recorded since migration 0166)."""
        ...

    def first_recorded_day(self, context: TenantContext) -> date | None:
        """The earliest UTC day with a daily row for the tenant; None when there is none yet."""
        ...


class InMemoryModelBudgetStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._budgets: dict[tuple[str, str | None], ModelBudget] = {}
        self._usage: dict[tuple[str, str, date], ModelUsageTotals] = {}
        self._daily: dict[tuple[str, str, UsageKey], ModelUsageTotals] = {}
        self._lock = RLock()

    @staticmethod
    def _budget_key(context: TenantContext, scope: BudgetScope) -> tuple[str, str | None]:
        return (context.tenant_id, context.workspace_id if scope == BudgetScope.WORKSPACE else None)

    def get_budget(self, context: TenantContext, scope: BudgetScope) -> ModelBudget | None:
        with self._lock:
            return self._budgets.get(self._budget_key(context, scope))

    def set_budget(
        self, context: TenantContext, scope: BudgetScope, limits: ModelBudgetLimits, now: datetime
    ) -> ModelBudget:
        budget = ModelBudget(scope=scope, updated_at=now, updated_by=context.user_id, **limits.model_dump())
        with self._lock:
            self._budgets[self._budget_key(context, scope)] = budget
        return budget

    def usage(self, context: TenantContext, scope: BudgetScope, month: date) -> ModelUsageTotals:
        with self._lock:
            rows = [
                totals
                for (tenant_id, workspace_id, row_month), totals in self._usage.items()
                if tenant_id == context.tenant_id
                and row_month == month
                and (scope == BudgetScope.TENANT or workspace_id == context.workspace_id)
            ]
            return _sum_totals(rows)

    def record_usage(
        self, context: TenantContext, month: date, delta: UsageDelta, now: datetime
    ) -> ModelUsageTotals:
        key = (context.tenant_id, context.workspace_id, month)
        with self._lock:
            updated = self._usage.get(key, ModelUsageTotals()).plus(delta)
            self._usage[key] = updated
            return updated

    def record_usage_detail(self, context: TenantContext, key: UsageKey, delta: UsageDelta, now: datetime) -> None:
        full = (context.tenant_id, context.workspace_id, key)
        with self._lock:
            self._daily[full] = self._daily.get(full, ModelUsageTotals()).plus(delta)

    def usage_breakdown(
        self, context: TenantContext, scope: BudgetScope, group_by: UsageGroup, start: date, end: date
    ) -> list[UsageBreakdownRow]:
        groups: dict[str, ModelUsageTotals] = {}

        def in_scope(tenant_id: str, workspace_id: str) -> bool:
            return tenant_id == context.tenant_id and (scope == BudgetScope.TENANT or workspace_id == context.workspace_id)

        with self._lock:
            if group_by == UsageGroup.MONTH:
                for (tenant_id, workspace_id, month), totals in self._usage.items():
                    if in_scope(tenant_id, workspace_id) and start <= month < end:
                        groups[month.isoformat()] = _sum_totals([groups.get(month.isoformat(), ModelUsageTotals()), totals])
            else:
                for (tenant_id, workspace_id, key), totals in self._daily.items():
                    if not in_scope(tenant_id, workspace_id) or not (start <= key.day < end):
                        continue
                    name = _group_key(key, group_by)
                    groups[name] = _sum_totals([groups.get(name, ModelUsageTotals()), totals])
        return [UsageBreakdownRow.of(name, totals) for name, totals in groups.items()]

    def first_recorded_day(self, context: TenantContext) -> date | None:
        with self._lock:
            days = [key.day for (tenant_id, _workspace_id, key) in self._daily if tenant_id == context.tenant_id]
        return min(days) if days else None

    def clear(self) -> None:
        with self._lock:
            self._budgets.clear()
            self._usage.clear()
            self._daily.clear()


def _group_key(key: UsageKey, group_by: UsageGroup) -> str:
    if group_by == UsageGroup.DAY:
        return key.day.isoformat()
    if group_by == UsageGroup.USER:
        return key.user_id
    if group_by == UsageGroup.AGENT:
        return key.agent_id
    return key.feature




def _sum_totals(rows: list[ModelUsageTotals]) -> ModelUsageTotals:
    return ModelUsageTotals(
        input_tokens=sum(row.input_tokens for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        estimated_cost_usd=sum(row.estimated_cost_usd for row in rows),
        calls=sum(row.calls for row in rows),
        unpriced_calls=sum(row.unpriced_calls for row in rows),
    )


memory_model_budget_store = InMemoryModelBudgetStore()


@contextmanager
def open_model_budget_store(context: TenantContext) -> Iterator[ModelBudgetStore]:
    """Budgets and usage for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.model_budget_repository import SqlAlchemyModelBudgetStore

        return SqlAlchemyModelBudgetStore(session)

    with open_scoped_store(context, memory_model_budget_store, sql_store) as store:
        yield store


# --------------------------------------------------------------------------- enforcement


def budget_exceeded_message(scope: BudgetScope, kind: BudgetKind, resets_on: date) -> str:
    """The user-facing refusal, built from the budget's fields rather than an exception's text."""
    owner = "organization" if scope == BudgetScope.TENANT else "workspace"
    measure = "estimated cost" if kind == BudgetKind.COST else "tokens"
    return (
        f"This {owner} has used its monthly model budget ({measure}). It resets on "
        f"{resets_on.isoformat()} (UTC); an owner can raise it in Settings."
    )


class ModelBudgetExceededError(Exception):
    """A tenant or workspace has used its model budget for the current UTC month.

    ``code`` is the API error code; subclasses (the emergency stop and per-agent caps in
    ``agent_controls.py``) override it and the message.
    """

    code = "model_budget_exceeded"

    def __init__(self, scope: BudgetScope, kind: BudgetKind, resets_on: date) -> None:
        self.scope = scope
        self.kind = kind
        self.resets_on = resets_on
        super().__init__(budget_exceeded_message(scope, kind, resets_on))

    @property
    def message(self) -> str:
        return str(self)

    def spoken(self, arabic: bool) -> str:
        """A short sentence for the voice assistant to say instead of an error."""
        if arabic:
            return (
                "وصلت مساحة العمل إلى حد استخدام النموذج لهذا الشهر، فلا أستطيع الإجابة الآن. "
                "يمكن للمالك رفع الحد من الإعدادات."
            )
        return (
            "This workspace has reached its model budget for the month, so I can't answer "
            "that right now. An owner can raise the budget in Settings."
        )


@dataclass(frozen=True)
class ScopeState:
    budget: ModelBudget | None
    usage: ModelUsageTotals

    def exceeded(self) -> BudgetKind | None:
        if self.budget is None:
            return None
        for kind in (BudgetKind.COST, BudgetKind.TOKENS):
            limit = self.budget.limit(kind)
            if limit is not None and self.usage.amount(kind) >= limit:
                return kind
        return None


def _scope_states(
    store: ModelBudgetStore, context: TenantContext, month: date
) -> dict[BudgetScope, ScopeState]:
    return {
        scope: ScopeState(store.get_budget(context, scope), store.usage(context, scope, month))
        for scope in BudgetScope
    }


def check_model_budget(
    context: TenantContext, *, now: datetime | None = None, agent_id: str | None = None
) -> None:
    """Raise :class:`ModelBudgetExceededError` when a model call may not start.

    The tenant's emergency stop comes first (``AgentsStoppedError``, a subclass), then the
    organization and workspace budgets, then ``agent_id``'s own monthly cap when the call is
    made for an agent (``AgentCapExceededError``, docs/agent-controls.md).
    """
    from .agent_controls import check_agent_cap, require_agents_running

    require_agents_running(context)
    month = month_start(now or utc_now())
    with open_model_budget_store(context) as store:
        states = _scope_states(store, context, month)
    for scope in (BudgetScope.TENANT, BudgetScope.WORKSPACE):
        kind = states[scope].exceeded()
        if kind is not None:
            telemetry.record_model_budget_rejection(scope.value, kind.value)
            logger.warning(
                "model_budget_exceeded scope=%s kind=%s tenant_id=%s workspace_id=%s",
                scope.value,
                kind.value,
                _log_safe(context.tenant_id),
                _log_safe(context.workspace_id),
                extra={
                    "anum_model_budget": {
                        "event": "exceeded",
                        "scope": scope.value,
                        "kind": kind.value,
                        "tenant_id": _log_safe(context.tenant_id),
                        "workspace_id": _log_safe(context.workspace_id),
                    }
                },
            )
            raise ModelBudgetExceededError(scope, kind, next_month_start(month))
    if agent_id is not None:
        check_agent_cap(context, agent_id, now=now)


_WARNED_UNPRICED: set[str] = set()


def _warn_unpriced_once(model: str) -> None:
    """One warning per model and process: its cost is assumed, so the owner should add a price."""
    if model in _WARNED_UNPRICED or len(_WARNED_UNPRICED) >= 200:
        return
    _WARNED_UNPRICED.add(model)
    logger.warning(
        "model_budget_unpriced_model model=%s: no price is configured, so calls under a cost limit are counted at the "
        "worst-case price (ANUM_MODEL_UNPRICED_PRICE); add the model to ANUM_MODEL_PRICES for a real estimate",
        _log_safe(model)[:120],
        extra={"anum_model_budget": {"event": "unpriced_model", "model": _log_safe(model)[:120]}},
    )


def _log_safe(value: str) -> str:
    """Identifiers can come from request headers in local mode: no forged log lines."""
    return value.replace("\r", "").replace("\n", "")


def record_model_usage(
    context: TenantContext,
    usage: ModelUsage,
    *,
    now: datetime | None = None,
    agent_id: str | None = None,
    feature: str | None = None,
) -> list[tuple[BudgetScope, BudgetKind, float]]:
    """Add one call's usage and return the ``(scope, kind, threshold)`` crossings.

    The call is also attributed to its UTC day, the caller (``context.user_id``), ``agent_id`` and
    ``feature`` (else the ``feature_scope`` in force, else ``other``) for the spend breakdown."""
    moment = now or utc_now()
    month = month_start(moment)
    delta = UsageDelta.of(usage)
    with open_model_budget_store(context) as store:
        budgets = {scope: store.get_budget(context, scope) for scope in BudgetScope}
        if delta.unpriced and any(budget is not None and budget.limit(BudgetKind.COST) is not None for budget in budgets.values()):
            # A cost limit applies and the model has no price: count a worst case, not zero.
            delta = delta.with_assumed_cost(usage.model)
        workspace_total = store.record_usage(context, month, delta, moment)
        store.record_usage_detail(
            context,
            UsageKey(
                day=moment.astimezone(timezone.utc).date(),
                user_id=context.user_id[:160],
                agent_id=(agent_id or "")[:80],
                feature=(feature or current_feature() or UNKNOWN_FEATURE)[:80],
            ),
            delta,
            moment,
        )
        tenant_total = (
            store.usage(context, BudgetScope.TENANT, month)
            if budgets[BudgetScope.TENANT] is not None
            else None
        )
    totals = {BudgetScope.WORKSPACE: workspace_total, BudgetScope.TENANT: tenant_total}
    crossings: list[tuple[BudgetScope, BudgetKind, float]] = []
    for scope, budget in budgets.items():
        total = totals[scope]
        if budget is None or total is None:
            continue
        for kind in BudgetKind:
            limit = budget.limit(kind)
            if not limit:
                continue
            after = total.amount(kind)
            before = after - delta.amount(kind)
            for threshold in THRESHOLDS:
                if before < threshold * limit <= after:
                    crossings.append((scope, kind, threshold))
                    _emit_threshold(context, scope, kind, threshold)
            # Owners hear about 50, 75, 90 and 100 percent in the app (docs/model-gateway.md#budget-alerts).
            from .budget_alerts import alert_scope, crossed

            for percent in crossed(before, after, float(limit), extra=budget.alert_percent):
                alert_scope(context, scope.value, kind.value, percent, float(limit), next_month_start(month))
    return crossings


def _emit_threshold(
    context: TenantContext, scope: BudgetScope, kind: BudgetKind, threshold: float
) -> None:
    percent = round(threshold * 100)
    telemetry.record_model_budget_threshold(scope.value, kind.value, percent)
    logger.warning(
        "model_budget_threshold scope=%s kind=%s percent=%d tenant_id=%s workspace_id=%s",
        scope.value,
        kind.value,
        percent,
        _log_safe(context.tenant_id),
        _log_safe(context.workspace_id),
        extra={
            "anum_model_budget": {
                "event": "threshold",
                "scope": scope.value,
                "kind": kind.value,
                "percent": percent,
                "tenant_id": _log_safe(context.tenant_id),
                "workspace_id": _log_safe(context.workspace_id),
            }
        },
    )


def _check_call(context: TenantContext, now: datetime, agent_id: str | None) -> None:
    """One worker-thread hop: the stop and budgets, then the calling agent's own cap."""
    check_model_budget(context, now=now)
    if agent_id is not None:
        from .agent_controls import check_agent_cap

        check_agent_cap(context, agent_id, now=now)


class BudgetedModelGateway:
    """Wraps a workspace's gateway: budget check before, usage recording after."""

    def __init__(
        self,
        inner: ModelGateway,
        context: TenantContext,
        *,
        clock: Callable[[], datetime] = utc_now,
        agent_id: str | None = None,
        feature: str | None = None,
    ) -> None:
        self.inner = inner
        self.context = context
        self._clock = clock
        # The agent profile this gateway's calls are made for (its monthly cap and usage).
        self.agent_id = agent_id
        # The feature the calls are made for (the spend breakdown); None reads ``feature_scope``.
        self.feature = feature

    def _variant(self, inner: ModelGateway, *, agent_id: str | None) -> BudgetedModelGateway:
        return BudgetedModelGateway(inner, self.context, clock=self._clock, agent_id=agent_id, feature=self.feature)

    def for_agent(self, agent_id: str | None) -> BudgetedModelGateway:
        """The same gateway, with every call attributed to ``agent_id`` and behind its cap."""
        if agent_id == self.agent_id:
            return self
        return self._variant(self.inner, agent_id=agent_id)

    def for_route(self, route: ModelRoute) -> BudgetedModelGateway:
        """The routed inner gateway, still behind this workspace's budget."""
        routed = route_gateway(self.inner, route)
        return self if routed is self.inner else self._variant(routed, agent_id=self.agent_id)

    def with_image(self, image: VisionImage) -> BudgetedModelGateway | None:
        """The inner gateway's vision variant (voice calls), still behind the budget."""
        variant = vision_gateway(self.inner, image)
        return None if variant is None else self._variant(variant, agent_id=self.agent_id)

    def with_tool_round(self, tool_round: Any) -> BudgetedModelGateway | None:
        """The inner gateway's tool round (voice grounding), still behind the budget; None
        when the provider has no client tool use. Defined here so ``__getattr__`` never
        hands out an unmetered inner gateway."""
        variant = getattr(self.inner, "with_tool_round", None)
        if not callable(variant):
            return None
        return self._variant(variant(tool_round), agent_id=self.agent_id)

    def __getattr__(self, name: str) -> Any:
        # provider, model, base_url ... of the wrapped gateway (voice checks `provider`).
        return getattr(self.inner, name)

    async def _check(self) -> None:
        # The budget and usage rows are database reads and writes: in a worker thread,
        # never on the event loop (docs/realtime.md#event-loop).
        await asyncio.to_thread(_check_call, self.context, self._clock(), self.agent_id)

    async def _record_async(self, response: ModelResponse) -> None:
        await asyncio.to_thread(self._record, response)

    def _record_soon(self, response: ModelResponse) -> None:
        try:
            asyncio.get_running_loop().run_in_executor(None, self._record, response)
        except RuntimeError:  # no running loop (interpreter shutdown): record inline
            self._record(response)

    def _record(self, response: ModelResponse) -> None:
        try:
            record_model_usage(
                self.context, response.usage, now=self._clock(), agent_id=self.agent_id, feature=self.feature
            )
            if self.agent_id is not None:
                from .agent_controls import record_agent_usage

                record_agent_usage(self.context, self.agent_id, response.usage, now=self._clock())
        except Exception as exc:  # the call already happened; never fail the user's request
            logger.warning(
                "model_budget_record_failed error_class=%s",
                type(exc).__name__,
                extra={"anum_model_budget": {"event": "record_failed", "error_class": type(exc).__name__}},
            )

    async def generate_text(self, prompt: str) -> ModelResponse:
        await self._check()
        response = await self.inner.generate_text(prompt)
        await self._record_async(response)
        return response

    async def generate_with_instructions(self, instructions: str, prompt: str) -> ModelResponse:
        """Stable instructions then a prompt (the planner's cache layout), budgeted.

        A gateway without the method gets them as one prompt."""
        await self._check()
        generate = getattr(self.inner, "generate_with_instructions", None)
        if callable(generate):
            response: ModelResponse = await generate(instructions, prompt)
        else:
            response = await self.inner.generate_text(f"{instructions}\n\n{prompt}")
        await self._record_async(response)
        return response

    async def generate_structured(
        self, prompt: str, response_model: type[StructuredModel]
    ) -> tuple[StructuredModel, ModelResponse]:
        await self._check()
        parsed, response = await self.inner.generate_structured(prompt, response_model)
        await self._record_async(response)
        return parsed, response

    async def complete_chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> ModelResponse:
        """A non-streaming chat call, checked before and metered after like the others.

        Gateways without ``complete_chat`` answer through ``stream_chat``, collected.
        """
        await self._check()
        complete = getattr(self.inner, "complete_chat", None)
        if complete is not None:
            response: ModelResponse = await complete(messages, max_tokens=max_tokens)
        else:
            parts: list[str] = []
            usage: list[ModelUsage] = []
            async for chunk in self.inner.stream_chat(messages, max_tokens=max_tokens, on_usage=usage.append):
                parts.append(chunk)
            text = "".join(parts)
            response = ModelResponse(
                text=text,
                usage=usage[-1]
                if usage
                else ModelUsage(
                    input_tokens=sum(estimate_tokens(m["content"]) for m in messages),
                    output_tokens=estimate_tokens(text),
                    provider=str(getattr(self.inner, "provider", "unknown")),
                    model=str(getattr(self.inner, "model", "unknown")),
                ),
            )
        await self._record_async(response)
        return response

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResponse:
        """Embeddings for retrieval count against the same monthly budget."""
        embed = getattr(self.inner, "embed", None)
        if embed is None:
            raise NotImplementedError("this model gateway cannot embed text")
        await self._check()
        response: EmbeddingResponse = await embed(texts, model=model)
        await self._record_async(ModelResponse(text="", usage=response.usage))
        return response

    async def stream_text(self, prompt: str) -> AsyncIterator[str]:
        async for chunk in self.stream_chat([{"role": "user", "content": prompt}]):
            yield chunk

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        on_usage: UsageCallback | None = None,
    ) -> AsyncIterator[str]:
        """Checked before the call; metered with the stream's usage when it ends (the
        provider's figures, or an estimate when it reports none or the stream stopped)."""
        await self._check()

        reported: list[ModelUsage] = []
        stopped = False

        def record(usage: ModelUsage) -> None:
            # Called from inside the provider stream: only noted here, written below. A
            # provider stream closed after this one stopped reports late: written then.
            if stopped:
                self._record_soon(ModelResponse(text="", usage=usage))
            else:
                reported.append(usage)
            if on_usage is not None:
                on_usage(usage)

        try:
            async for chunk in self.inner.stream_chat(messages, max_tokens=max_tokens, on_usage=record):
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            stopped = True
            if reported:
                # Stopped: never await here (a cancelled stream must end now); the write
                # runs in a worker thread on its own.
                self._record_soon(ModelResponse(text="", usage=reported[-1]))
            raise
        except Exception:
            if reported:
                await self._record_async(ModelResponse(text="", usage=reported[-1]))
            raise
        if reported:
            await self._record_async(ModelResponse(text="", usage=reported[-1]))


# --------------------------------------------------------------------------- API

router = APIRouter(prefix="/api/v1/model-budgets", tags=["model-budgets"])


class ModelBudgetScopeView(BaseModel):
    budget: ModelBudget | None
    usage: ModelUsageTotals
    total_tokens: int
    exceeded: bool


class ModelBudgetOverview(BaseModel):
    period_start: date
    resets_on: date
    tenant: ModelBudgetScopeView
    workspace: ModelBudgetScopeView
    # Whether this caller may change the organization (tenant) budget: the organization
    # administrator role. Clients show the tenant budget read-only when it is false.
    can_manage_tenant_budget: bool = False


def _overview(context: TenantContext, now: datetime) -> ModelBudgetOverview:
    month = month_start(now)
    with open_model_budget_store(context) as store:
        states = _scope_states(store, context, month)

    def view(state: ScopeState) -> ModelBudgetScopeView:
        return ModelBudgetScopeView(
            budget=state.budget,
            usage=state.usage,
            total_tokens=state.usage.total_tokens,
            exceeded=state.exceeded() is not None,
        )

    return ModelBudgetOverview(
        period_start=month,
        resets_on=next_month_start(month),
        tenant=view(states[BudgetScope.TENANT]),
        workspace=view(states[BudgetScope.WORKSPACE]),
        can_manage_tenant_budget=has_permission(context, Permission.ORG_POLICY_MANAGE),
    )


def _describe_limits(budget: ModelBudget | None) -> str:
    if budget is None:
        return "none"
    cost = budget.monthly_cost_limit_usd
    tokens = budget.monthly_token_limit
    alert = budget.alert_percent
    return (
        f"cost_usd={'none' if cost is None else cost} tokens={'none' if tokens is None else tokens} "
        f"alert_percent={'none' if alert is None else alert}"
    )


# --------------------------------------------------------------------------- spend breakdown

MAX_BREAKDOWN_DAYS = 400
BREAKDOWN_MAX_LIMIT = 500
BREAKDOWN_CSV_LIMIT = 10_000


class ModelUsageBreakdown(BaseModel):
    scope: BudgetScope
    group_by: UsageGroup
    from_day: date
    to_day: date
    rows: list[UsageBreakdownRow]
    total: ModelUsageTotals
    # How many groups the range holds in all; ``truncated`` when ``rows`` is not the end of them.
    count: int
    limit: int
    offset: int
    truncated: bool
    # The daily rows exist since migration 0166: a range that starts before the first recorded day
    # shows less than the monthly figures for those days. Null when every day of the range is covered.
    recorded_since: date | None = None


def _breakdown_range(group_by: UsageGroup, from_day: date | None, to_day: date | None, now: datetime) -> tuple[date, date]:
    """``[from, to]`` as the caller asked (both inclusive), defaulting to this UTC month (``day``, ``user``,
    ``agent``, ``feature``) or the last twelve months (``month``)."""
    today = now.astimezone(timezone.utc).date()
    if group_by == UsageGroup.MONTH:
        start = from_day or date(today.year - 1, today.month, 1)
        end = to_day or today
    else:
        start = from_day or month_start(now)
        end = to_day or today
    if end < start:
        raise HTTPException(status_code=422, detail="`to` must not be before `from`")
    if (end - start).days > MAX_BREAKDOWN_DAYS:
        raise HTTPException(status_code=422, detail=f"The range may cover at most {MAX_BREAKDOWN_DAYS} days")
    return start, end


def _sort_rows(rows: list[UsageBreakdownRow], group_by: UsageGroup) -> list[UsageBreakdownRow]:
    if group_by in (UsageGroup.DAY, UsageGroup.MONTH):
        return sorted(rows, key=lambda row: row.key)
    return sorted(rows, key=lambda row: (-row.estimated_cost_usd, -row.total_tokens, row.key))


def _agent_labels(context: TenantContext, rows: list[UsageBreakdownRow]) -> None:
    """Name the agents in ``rows`` (the default assistant and the profiles this caller may see)."""
    from .agent_controls import DEFAULT_ASSISTANT_ID, DEFAULT_ASSISTANT_NAME
    from .agent_profiles import open_agent_store

    names = {DEFAULT_ASSISTANT_ID: DEFAULT_ASSISTANT_NAME}
    try:
        with open_agent_store(context) as agents:
            names.update({profile.id: profile.name for profile in agents.list(context)})
    except Exception as exc:  # noqa: BLE001 - names are a convenience; the ids still show
        logger.warning("model_budget_agent_names_failed error_class=%s", type(exc).__name__)
    for row in rows:
        row.label = names.get(row.key)


def _recorded_since(store: ModelBudgetStore, context: TenantContext, start: date) -> date | None:
    """The first UTC day with a daily row for this organization, when ``start`` is before it."""
    since = store.first_recorded_day(context)
    if since is None or since <= start:
        return None
    return since


def usage_breakdown(
    context: TenantContext,
    *,
    scope: BudgetScope,
    group_by: UsageGroup,
    from_day: date | None,
    to_day: date | None,
    limit: int,
    offset: int,
    now: datetime | None = None,
) -> ModelUsageBreakdown:
    moment = now or utc_now()
    start, end = _breakdown_range(group_by, from_day, to_day, moment)
    with open_model_budget_store(context) as store:
        rows = store.usage_breakdown(context, scope, group_by, start, end + timedelta(days=1))
        recorded_since = None if group_by == UsageGroup.MONTH else _recorded_since(store, context, start)
    rows = _sort_rows(rows, group_by)
    if group_by == UsageGroup.AGENT:
        _agent_labels(context, rows)
    page = rows[offset : offset + limit]
    return ModelUsageBreakdown(
        scope=scope,
        group_by=group_by,
        from_day=start,
        to_day=end,
        rows=page,
        total=_sum_totals([row.totals() for row in rows]),
        count=len(rows),
        limit=limit,
        offset=offset,
        truncated=offset + len(page) < len(rows),
        recorded_since=recorded_since,
    )


def breakdown_csv(breakdown: ModelUsageBreakdown) -> str:
    """The page as CSV: the group key, its label, and the five figures plus the token total."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([breakdown.group_by.value, "label", "calls", "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd", "unpriced_calls"])
    for row in breakdown.rows:
        writer.writerow([
            _csv_safe(row.key), _csv_safe(row.label or ""), row.calls, row.input_tokens, row.output_tokens,
            row.total_tokens, f"{row.estimated_cost_usd:.6f}", row.unpriced_calls,
        ])
    return buffer.getvalue()


def _csv_safe(value: str) -> str:
    """A cell that a spreadsheet cannot read as a formula (user and agent ids come from outside)."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("", response_model=ModelBudgetOverview)
async def get_model_budgets(context: TenantContext = Depends(tenant_context)) -> ModelBudgetOverview:
    """The organization's and this workspace's budgets and usage this month (owners)."""
    require_permission(context, Permission.ORGANIZATION_MANAGE)
    return _overview(context, utc_now())


@router.get("/usage", response_model=ModelUsageBreakdown, responses={200: {"content": {"text/csv": {}}}})
def get_model_usage(
    group_by: UsageGroup = Query(default=UsageGroup.DAY),
    scope: BudgetScope = Query(default=BudgetScope.WORKSPACE),
    from_day: date | None = Query(default=None, alias="from"),
    to_day: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=BREAKDOWN_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    format: str = Query(default="json", pattern="^(json|csv)$"),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    """The recorded model spend by day, month, person, agent or feature (owners), paged, or as CSV.

    ``scope=tenant`` sums every workspace of the organization (any workspace owner may read it,
    as the overview already shows the organization total). Nothing is estimated: the rows are the
    usage the budgeted gateway recorded after each call.
    """
    require_permission(context, Permission.ORGANIZATION_MANAGE)
    if format == "csv":
        # One file holds the whole range (up to the CSV cap), whatever page was asked for.
        breakdown = usage_breakdown(
            context, scope=scope, group_by=group_by, from_day=from_day, to_day=to_day, limit=BREAKDOWN_CSV_LIMIT, offset=0
        )
        name = f"model-usage-{scope.value}-{group_by.value}-{breakdown.from_day.isoformat()}-{breakdown.to_day.isoformat()}.csv"
        return Response(
            content=breakdown_csv(breakdown),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
        )
    breakdown = usage_breakdown(context, scope=scope, group_by=group_by, from_day=from_day, to_day=to_day, limit=limit, offset=offset)
    return Response(content=breakdown.model_dump_json(), media_type="application/json")


@router.put("/{scope}", response_model=ModelBudgetOverview)
def set_model_budget(
    scope: BudgetScope,
    payload: ModelBudgetLimits,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ModelBudgetOverview:
    """Set (or clear, with nulls) the organization's or this workspace's monthly budget.

    The organization budget caps every workspace of the tenant, so it needs the organization
    administrator role; this workspace's budget stays with the workspace owner.
    """
    require_permission(
        context,
        Permission.ORG_POLICY_MANAGE if scope == BudgetScope.TENANT else Permission.ORGANIZATION_MANAGE,
    )
    now = utc_now()
    with open_model_budget_store(context) as store:
        previous = store.get_budget(context, scope)
        budget = store.set_budget(context, scope, payload, now)
    target = (
        f"model_budget:tenant:{context.tenant_id}"
        if scope == BudgetScope.TENANT
        else f"model_budget:workspace:{context.workspace_id}"
    )
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action="model_budget.update",
            target=target,
            outcome="success",
            correlation_id=new_id("correlation"),
            created_at=now,
            # Rendered as text: audit metadata redacts any key that mentions "token".
            metadata={
                "scope": scope.value,
                "previous_limits": _describe_limits(previous),
                "limits": _describe_limits(budget),
            },
        )
    )
    return _overview(context, now)
