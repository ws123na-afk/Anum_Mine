"""Temporal workflow for durable agent runs (docs/agent-runtime.md).

This module is loaded inside Temporal's deterministic workflow sandbox, so it imports
only the standard library and ``temporalio``. All I/O happens in the
``anum.advance_run`` activity (``anum_api.durable_runs``), which advances a run by
one step from its persisted checkpoint. Because every step starts from what
PostgreSQL holds, a run survives a worker restart: Temporal retries the activity
on another worker and it continues where the checkpoint says.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

WORKFLOW_NAME = "AgentRunWorkflow"
ADVANCE_ACTIVITY = "anum.advance_run"
APPROVAL_SIGNAL = "approval_decided"
CANCEL_SIGNAL = "cancel"
TERMINAL_PHASES = frozenset({"completed", "failed", "cancelled"})
# A workflow never needs more than plan -> approval -> execute; the cap turns a
# logic error into a visible failure instead of an endless loop.
MAX_STEPS = 20


@dataclass
class AgentRunInput:
    """Explicit tenant context for every activity; nothing is read from ambient state."""

    tenant_id: str
    workspace_id: str
    user_id: str
    task_id: str
    run_id: str
    roles: list[str] = field(default_factory=list)
    # Activity timeouts are inputs so tests can shorten them; production uses defaults.
    activity_timeout_seconds: float = 600.0
    approval_poll_seconds: float = 3600.0


@dataclass
class AgentRunState:
    run_id: str
    phase: str
    status: str
    approval_id: str | None = None


def workflow_id_for(tenant_id: str, workspace_id: str, task_id: str) -> str:
    """Deterministic workflow id, so one task never has two live workflows."""
    return f"anum-run/{tenant_id}/{workspace_id}/{task_id}"


@workflow.defn(name=WORKFLOW_NAME)
class AgentRunWorkflow:
    def __init__(self) -> None:
        self._decided: set[str] = set()
        self._cancelled = False

    @workflow.signal(name=APPROVAL_SIGNAL)
    def approval_decided(self, approval_id: str) -> None:
        self._decided.add(approval_id)

    @workflow.signal(name=CANCEL_SIGNAL)
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.run
    async def run(self, request: AgentRunInput) -> AgentRunState:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
            non_retryable_error_types=["RunNotFound", "InvalidRunState"],
        )
        steps = 0
        while True:
            state: AgentRunState = await workflow.execute_activity(
                ADVANCE_ACTIVITY,
                request,
                result_type=AgentRunState,
                start_to_close_timeout=timedelta(seconds=request.activity_timeout_seconds),
                retry_policy=retry,
            )
            if state.phase in TERMINAL_PHASES:
                return state
            if state.phase == "waiting_approval":
                approval_id = state.approval_id
                if self._cancelled:
                    # The API marks the task cancelled before signalling; give that a
                    # moment, then advancing observes it and returns the terminal state.
                    await asyncio.sleep(5)
                    continue
                try:
                    await workflow.wait_condition(
                        lambda: self._cancelled or approval_id in self._decided,
                        timeout=timedelta(seconds=request.approval_poll_seconds),
                    )
                except asyncio.TimeoutError:
                    # No signal (for example the API stopped right after committing
                    # the decision): advancing re-reads the approval from the database.
                    pass
                continue
            steps += 1
            if steps >= MAX_STEPS:
                raise ApplicationError(
                    f"Run {request.run_id} did not settle in {MAX_STEPS} steps",
                    type="InvalidRunState",
                    non_retryable=True,
                )
