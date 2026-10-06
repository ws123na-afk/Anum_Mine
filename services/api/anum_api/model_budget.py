"""Per-tenant and per-workspace monthly model budgets (threat model G4).

An owner sets, for the organization (tenant) and for each workspace, an optional
estimated-cost limit in USD and an optional token limit per UTC calendar month. Every
model call made for a workspace (task planning and voice answers) goes through
:class:`BudgetedModelGateway`, which:

1. refuses the call (text generation and retrieval embeddings alike) before it reaches the provider when the tenant or the workspace has
   already used its budget for the current month (:class:`ModelBudgetExceededError`,
   answered as HTTP 402 by the API and as a spoken sentence by voice);
2. after a successful call adds the gateway's usage metadata (input/output tokens and
   ``estimated_cost_usd``) to the workspace's monthly usage row;
3. logs ``model_budget_threshold`` and counts ``anum.model.budget.thresholds`` when a
   call crosses 80% or 100% of a limit.

The check does not reserve spend, so concurrent calls admitted just under a limit can
overshoot it by at most those calls' usage. Calls whose model has no price estimate
count tokens but no cost (``unpriced_calls``). Usage is stored per workspace and month;
tenant usage is the sum of its workspaces.

``ANUM_REPOSITORY_BACKEND=memory`` keeps budgets and usage in process memory; with
``postgresql`` they live in the RLS-protected ``model_budgets`` and
``model_usage_monthly`` tables (migration ``0009_model_budgets``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import repository_context, require_permission, tenant_context
from .model_gateway import EmbeddingResponse, ModelGateway, ModelResponse, ModelUsage, StructuredModel
from .repository import AnumRepository
from .schemas import TenantContext, new_id
from .scoped_store import open_scoped_store
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


@dataclass(frozen=True)
class UsageDelta:
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None

    @classmethod
    def of(cls, usage: ModelUsage) -> UsageDelta:
        return cls(usage.input_tokens, usage.output_tokens, usage.estimated_cost_usd)

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


class InMemoryModelBudgetStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._budgets: dict[tuple[str, str | None], ModelBudget] = {}
        self._usage: dict[tuple[str, str, date], ModelUsageTotals] = {}
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
            current = self._usage.get(key, ModelUsageTotals())
            updated = ModelUsageTotals(
                input_tokens=current.input_tokens + delta.input_tokens,
                output_tokens=current.output_tokens + delta.output_tokens,
                estimated_cost_usd=current.estimated_cost_usd + (delta.estimated_cost_usd or 0.0),
                calls=current.calls + 1,
                unpriced_calls=current.unpriced_calls + (delta.estimated_cost_usd is None),
            )
            self._usage[key] = updated
            return updated

    def clear(self) -> None:
        with self._lock:
            self._budgets.clear()
            self._usage.clear()


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


class ModelBudgetExceededError(Exception):
    """A tenant or workspace has used its model budget for the current UTC month."""

    def __init__(self, scope: BudgetScope, kind: BudgetKind, resets_on: date) -> None:
        self.scope = scope
        self.kind = kind
        self.resets_on = resets_on
        owner = "organization" if scope == BudgetScope.TENANT else "workspace"
        measure = "estimated cost" if kind == BudgetKind.COST else "tokens"
        super().__init__(
            f"This {owner} has used its monthly model budget ({measure}). It resets on "
            f"{resets_on.isoformat()} (UTC); an owner can raise it in Settings."
        )

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


def check_model_budget(context: TenantContext, *, now: datetime | None = None) -> None:
    """Raise :class:`ModelBudgetExceededError` when a model call may not start."""
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



def _log_safe(value: str) -> str:
    """Identifiers can come from request headers in local mode: no forged log lines."""
    return value.replace("\r", "").replace("\n", "")


def record_model_usage(
    context: TenantContext, usage: ModelUsage, *, now: datetime | None = None
) -> list[tuple[BudgetScope, BudgetKind, float]]:
    """Add one call's usage and return the ``(scope, kind, threshold)`` crossings."""
    month = month_start(now or utc_now())
    delta = UsageDelta.of(usage)
    with open_model_budget_store(context) as store:
        workspace_total = store.record_usage(context, month, delta, now or utc_now())
        budgets = {scope: store.get_budget(context, scope) for scope in BudgetScope}
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


class BudgetedModelGateway:
    """Wraps a workspace's gateway: budget check before, usage recording after."""

    def __init__(
        self,
        inner: ModelGateway,
        context: TenantContext,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.inner = inner
        self.context = context
        self._clock = clock

    def __getattr__(self, name: str) -> Any:
        # provider, model, base_url ... of the wrapped gateway (voice checks `provider`).
        return getattr(self.inner, name)

    def _record(self, response: ModelResponse) -> None:
        try:
            record_model_usage(self.context, response.usage, now=self._clock())
        except Exception as exc:  # the call already happened; never fail the user's request
            logger.warning(
                "model_budget_record_failed error_class=%s",
                type(exc).__name__,
                extra={"anum_model_budget": {"event": "record_failed", "error_class": type(exc).__name__}},
            )

    async def generate_text(self, prompt: str) -> ModelResponse:
        check_model_budget(self.context, now=self._clock())
        response = await self.inner.generate_text(prompt)
        self._record(response)
        return response

    async def generate_structured(
        self, prompt: str, response_model: type[StructuredModel]
    ) -> tuple[StructuredModel, ModelResponse]:
        check_model_budget(self.context, now=self._clock())
        parsed, response = await self.inner.generate_structured(prompt, response_model)
        self._record(response)
        return parsed, response

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResponse:
        """Embeddings for retrieval count against the same monthly budget."""
        embed = getattr(self.inner, "embed", None)
        if embed is None:
            raise NotImplementedError("this model gateway cannot embed text")
        check_model_budget(self.context, now=self._clock())
        response: EmbeddingResponse = await embed(texts, model=model)
        try:
            record_model_usage(self.context, response.usage, now=self._clock())
        except Exception as exc:  # the call already happened
            logger.warning(
                "model_budget_record_failed error_class=%s",
                type(exc).__name__,
                extra={"anum_model_budget": {"event": "record_failed", "error_class": type(exc).__name__}},
            )
        return response

    async def stream_text(self, prompt: str) -> AsyncIterator[str]:
        # Checked, but not metered: the gateway does not report usage for streams yet.
        check_model_budget(self.context, now=self._clock())
        async for chunk in self.inner.stream_text(prompt):
            yield chunk


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
    )


def _describe_limits(budget: ModelBudget | None) -> str:
    if budget is None:
        return "none"
    cost = budget.monthly_cost_limit_usd
    tokens = budget.monthly_token_limit
    return f"cost_usd={'none' if cost is None else cost} tokens={'none' if tokens is None else tokens}"


@router.get("", response_model=ModelBudgetOverview)
async def get_model_budgets(context: TenantContext = Depends(tenant_context)) -> ModelBudgetOverview:
    """The organization's and this workspace's budgets and usage this month (owners)."""
    require_permission(context, Permission.ORGANIZATION_MANAGE)
    return _overview(context, utc_now())


@router.put("/{scope}", response_model=ModelBudgetOverview)
async def set_model_budget(
    scope: BudgetScope,
    payload: ModelBudgetLimits,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ModelBudgetOverview:
    """Set (or clear, with nulls) the organization's or this workspace's monthly budget."""
    require_permission(context, Permission.ORGANIZATION_MANAGE)
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
