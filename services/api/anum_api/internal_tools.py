"""ANUM's "Now" internal tool set: memory_search, memory_write, task_status_lookup.

Each tool wraps a real repository interface (`memory.MemoryRepository`,
`repository.AnumRepository`) so it operates against the same data those
repositories back for the rest of the platform, rather than a fake. Handlers
translate expected domain failures (missing task, bad input) into
`ToolResult(status=RECOVERABLE_FAILURE)` rather than raising, per the tool
contract's result-shape contract; unexpected exceptions still propagate and
are caught by `tools.execute_tool`'s retry loop.
"""

from __future__ import annotations

from typing import Any

from .memory import (
    MemoryListFilters,
    MemoryNote,
    MemoryProvenance,
    MemoryRepository,
    RetentionPolicy,
)
from .repository import AnumRepository
from .schemas import RiskLevel, new_id, utc_now
from .tools import (
    RetryPolicy,
    ToolContract,
    ToolExecutionContext,
    ToolHandler,
    ToolRegistry,
    ToolResult,
    ToolResultStatus,
)


MEMORY_SEARCH_CONTRACT = ToolContract(
    name="memory_search",
    description="Search workspace memory notes visible to the current tenant/workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "query": {"type": "string"},
            "source_types": {"type": "array", "items": {"type": "string"}},
            "include_expired": {"type": "boolean"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {"notes": {"type": "array"}},
    },
    required_scopes=["memory:read"],
    risk_level=RiskLevel.LOW,
    timeout_seconds=5.0,
    retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.1),
    idempotent=True,
)

MEMORY_WRITE_CONTRACT = ToolContract(
    name="memory_write",
    description="Create a new, durable memory note for a task.",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "content": {"type": "string"},
            "source_type": {"type": "string"},
            "source_id": {"type": "string"},
            "source_metadata": {"type": "object"},
        },
        "required": ["task_id", "content"],
    },
    output_schema={
        "type": "object",
        "properties": {"note": {"type": "object"}},
    },
    required_scopes=["memory:create"],
    # Medium per approvals-and-risk.md: "actions that ... create durable
    # records."
    risk_level=RiskLevel.MEDIUM,
    timeout_seconds=5.0,
    retry_policy=RetryPolicy(max_attempts=1),
    idempotent=False,
)

TASK_STATUS_LOOKUP_CONTRACT = ToolContract(
    name="task_status_lookup",
    description="Look up the current status of a task by id.",
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    },
    output_schema={
        "type": "object",
        "properties": {"task": {"type": "object"}},
    },
    required_scopes=["task:read"],
    risk_level=RiskLevel.LOW,
    timeout_seconds=5.0,
    retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.1),
    idempotent=True,
)


def _build_memory_search_handler(repository: MemoryRepository) -> ToolHandler:
    async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        try:
            filters = MemoryListFilters(
                task_id=inputs.get("task_id"),
                query=inputs.get("query"),
                source_types=set(inputs.get("source_types") or []),
                include_expired=bool(inputs.get("include_expired", False)),
            )
        except Exception as exc:  # noqa: BLE001 - bad tool input, not a bug
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"invalid memory_search input: {exc}",
            )
        notes = repository.list(context.tenant, filters, utc_now())
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            output={"notes": [note.model_dump(mode="json") for note in notes]},
        )

    return handler


def _build_memory_write_handler(repository: MemoryRepository) -> ToolHandler:
    async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        task_id = inputs.get("task_id")
        content = inputs.get("content")
        if not task_id or not content:
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message="memory_write requires both task_id and content",
            )
        now = utc_now()
        note = MemoryNote(
            id=new_id("memory"),
            tenant_id=context.tenant.tenant_id,
            workspace_id=context.tenant.workspace_id,
            task_id=task_id,
            content=content,
            provenance=MemoryProvenance(
                source_type=str(inputs.get("source_type") or "tool:memory_write"),
                source_id=inputs.get("source_id"),
                created_by_user_id=context.tenant.user_id,
                created_at=now,
                metadata=inputs.get("source_metadata") or {},
            ),
            retention=RetentionPolicy(),
            created_at=now,
        )
        created = repository.create(note)
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            output={"note": created.model_dump(mode="json")},
        )

    return handler


def _build_task_status_lookup_handler(repository: AnumRepository) -> ToolHandler:
    async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        task_id = inputs.get("task_id")
        if not task_id:
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message="task_status_lookup requires task_id",
            )
        task = repository.get_task(task_id, context.tenant)
        if task is None:
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"task not found: {task_id}",
            )
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            output={"task": task.model_dump(mode="json")},
        )

    return handler


def build_internal_tool_registry(
    memory_repository: MemoryRepository,
    task_repository: AnumRepository,
) -> ToolRegistry:
    """Register ANUM's three "Now" internal tools against real repositories."""

    registry = ToolRegistry()
    registry.register(MEMORY_SEARCH_CONTRACT, _build_memory_search_handler(memory_repository))
    registry.register(MEMORY_WRITE_CONTRACT, _build_memory_write_handler(memory_repository))
    registry.register(
        TASK_STATUS_LOOKUP_CONTRACT, _build_task_status_lookup_handler(task_repository)
    )
    return registry
