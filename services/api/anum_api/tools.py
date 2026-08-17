"""The tool contract, registry, and approval-aware execution mediator.

Every tool call in ANUM -- internal, REST-integration, or MCP-discovered --
is described by a `ToolContract` and executed through `execute_tool()`. That
single entrypoint is what applies risk-based approval gating and builds the
audit trail, so no caller can invoke a tool handler directly and skip
mediation (see `internal_tools.py`, `rest_integration.py`, and
`mcp_adapter.py` for the three "Now" adapter shapes that all register into a
`ToolRegistry` and go through this same path).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit import REDACTED, AuditRecord, AuditRecorder
from .schemas import Approval, ApprovalStatus, RiskLevel, TenantContext, new_id, utc_now


_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


class ToolResultStatus(StrEnum):
    """The four outcomes the doc requires a tool result to distinguish."""

    SUCCESS = "success"
    RECOVERABLE_FAILURE = "recoverable_failure"
    BLOCKED = "blocked"
    PARTIAL = "partial"


class ToolResult(BaseModel):
    """The outcome of one tool execution attempt."""

    model_config = ConfigDict(frozen=True)

    status: ToolResultStatus
    output: dict[str, Any] | None = None
    error_message: str | None = None
    partial_output: dict[str, Any] | None = None


class RetryPolicy(BaseModel):
    """How many times, and how, a tool call may be retried.

    `max_attempts=1` (the default) means no retry: a single attempt only.
    Conceptually mirrors the scope/fingerprint idea in `idempotency.py` --
    retries are only ever safe to the degree the tool's `idempotent` flag
    says they are, though this module does not itself integrate with the
    idempotency store.
    """

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=20)
    backoff_seconds: float = Field(default=0.0, ge=0.0, le=300.0)


class ToolContract(BaseModel):
    """Everything the doc says a tool must declare about itself."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_scopes: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    timeout_seconds: float = Field(gt=0, le=600)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    idempotent: bool
    audit_redact_fields: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError(
                "tool name must be a lowercase slug: letters, digits, '_', '.', ':', '-', "
                "starting with a letter (max 64 chars)"
            )
        return value


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Identity and correlation data threaded through one tool call."""

    tenant: TenantContext
    correlation_id: str
    actor_id: str
    # Optional: only meaningful when the tool call is part of a task/agent
    # run. Falls back to correlation_id when an Approval needs a task_id
    # (Approval.task_id is required by the shared schema) but the tool call
    # itself is not tied to any task.
    task_id: str | None = None

    @property
    def approval_task_id(self) -> str:
        return self.task_id or self.correlation_id


ToolHandler: TypeAlias = Callable[[dict[str, Any], ToolExecutionContext], Awaitable[ToolResult]]


class ToolNotFoundError(LookupError):
    """Raised when `execute_tool` is asked for a name the registry doesn't have."""


class ToolRegistry:
    """In-memory catalog of tool contracts and their handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolContract, ToolHandler]] = {}

    def register(self, contract: ToolContract, handler: ToolHandler) -> None:
        if contract.name in self._tools:
            raise ValueError(f"tool already registered: {contract.name}")
        self._tools[contract.name] = (contract, handler)

    def get(self, name: str) -> tuple[ToolContract, ToolHandler] | None:
        return self._tools.get(name)

    def list_tools(self) -> tuple[ToolContract, ...]:
        return tuple(contract for contract, _handler in self._tools.values())


async def execute_tool(
    registry: ToolRegistry,
    name: str,
    inputs: dict[str, Any],
    context: ToolExecutionContext,
    *,
    repository_for_approval: Any = None,
    audit_recorder: AuditRecorder | None = None,
) -> tuple[ToolResult | Approval, AuditRecord]:
    """The mediation entrypoint every tool call must go through.

    - `risk_level == BLOCKED` always short-circuits to a `ToolResult` with
      `status=BLOCKED`; the handler is never called.
    - `risk_level == HIGH` short-circuits to a pending `schemas.Approval`
      (mirroring `runtime.AgentRuntime.run_task`'s approval pause); the
      handler is never called until a separate approval flow decides it.
    - `risk_level` LOW or MEDIUM actually invokes the handler, under a real
      `asyncio.wait_for` timeout and the contract's retry policy.

    Every attempt -- executed, blocked, or turned into an approval --
    produces one `AuditRecord`. If `audit_recorder` is given, the record is
    also persisted via its `.record()`; either way it is returned alongside
    the result so callers can always inspect it.
    """

    entry = registry.get(name)
    if entry is None:
        raise ToolNotFoundError(name)
    contract, handler = entry

    if contract.risk_level == RiskLevel.BLOCKED:
        result = ToolResult(
            status=ToolResultStatus.BLOCKED,
            error_message=f"Tool '{contract.name}' is blocked by policy and cannot execute.",
        )
        record = _build_audit_record(contract, context, outcome="blocked", inputs=inputs)
        _maybe_record(audit_recorder, record)
        return result, record

    if contract.risk_level == RiskLevel.HIGH:
        approval = Approval(
            id=new_id("approval"),
            task_id=context.approval_task_id,
            action=f"tool:{contract.name}",
            risk_level=RiskLevel.HIGH,
            status=ApprovalStatus.PENDING,
            reason=(
                f"Tool '{contract.name}' is high risk and requires approval "
                "before it can run."
            ),
            created_at=utc_now(),
        )
        if repository_for_approval is not None:
            repository_for_approval.save_approval(approval)
        record = _build_audit_record(
            contract, context, outcome="approval_required", inputs=inputs
        )
        _maybe_record(audit_recorder, record)
        return approval, record

    result = await _execute_with_retry(contract, handler, inputs, context)
    extra = {"error_message": result.error_message} if result.error_message else None
    record = _build_audit_record(
        contract, context, outcome=result.status.value, inputs=inputs, extra=extra
    )
    _maybe_record(audit_recorder, record)
    return result, record


async def _execute_with_retry(
    contract: ToolContract,
    handler: ToolHandler,
    inputs: dict[str, Any],
    context: ToolExecutionContext,
) -> ToolResult:
    attempts = contract.retry_policy.max_attempts
    last_result: ToolResult | None = None
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = await asyncio.wait_for(
                handler(inputs, context), timeout=contract.timeout_seconds
            )
        except asyncio.TimeoutError:
            last_result = None
            last_error = (
                f"Tool '{contract.name}' timed out after {contract.timeout_seconds}s "
                f"(attempt {attempt}/{attempts})"
            )
        except Exception as exc:  # noqa: BLE001 - handler failures become RECOVERABLE_FAILURE
            last_result = None
            last_error = f"Tool '{contract.name}' raised {type(exc).__name__}: {exc}"
        else:
            if result.status != ToolResultStatus.RECOVERABLE_FAILURE:
                return result
            last_result = result
            last_error = result.error_message

        if attempt < attempts and contract.retry_policy.backoff_seconds > 0:
            await asyncio.sleep(contract.retry_policy.backoff_seconds)

    if last_result is not None:
        return last_result
    return ToolResult(
        status=ToolResultStatus.RECOVERABLE_FAILURE,
        error_message=last_error or f"Tool '{contract.name}' failed with no result",
    )


def _maybe_record(recorder: AuditRecorder | None, record: AuditRecord) -> None:
    if recorder is not None:
        recorder.record(record)


def _build_audit_record(
    contract: ToolContract,
    context: ToolExecutionContext,
    *,
    outcome: str,
    inputs: dict[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> AuditRecord:
    redacted_inputs = _redact_extra_fields(inputs, frozenset(contract.audit_redact_fields))
    metadata: dict[str, Any] = {"tool": contract.name, "inputs": redacted_inputs}
    if extra:
        metadata.update(extra)
    return AuditRecord(
        id=new_id("audit"),
        tenant_id=context.tenant.tenant_id,
        workspace_id=context.tenant.workspace_id,
        actor=context.actor_id,
        action=f"tool.{contract.name}",
        target=f"tool:{contract.name}",
        outcome=outcome,
        correlation_id=context.correlation_id,
        created_at=utc_now(),
        metadata=metadata,
    )


def _redact_extra_fields(value: Any, field_names: frozenset[str]) -> Any:
    """Redact contract-declared field names beyond what `is_secret_key` catches.

    `AuditRecord` itself already redacts anything `events.is_secret_key`
    recognizes (see `audit.redact_metadata`); this only needs to handle the
    extra, tool-specific names a contract calls out via
    `audit_redact_fields`, since those may not look like secrets by name
    alone (e.g. a free-text field a particular tool happens to treat as
    sensitive).
    """

    if not field_names:
        return value
    normalized = {name.casefold() for name in field_names}
    return _redact_walk(value, normalized)


def _redact_walk(value: Any, normalized_names: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                REDACTED
                if str(key).casefold() in normalized_names
                else _redact_walk(item, normalized_names)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_walk(item, normalized_names) for item in value]
    return value
