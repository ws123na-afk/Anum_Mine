"""Task list filters, task permissions and the run-log export (docs/tasks.md).

Pure helpers shared by the routes in ``main.py`` and both repositories. Nothing here
reads storage: the repositories apply :class:`TaskFilters` in SQL (PostgreSQL) or with
:meth:`TaskFilters.matches` (in-memory), so both backends answer the same rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .approval_integrity import display_arguments
from .schemas import AgentRun, Approval, DomainEvent, Task, TaskStatus

SEARCH_MAX_CHARS = 200
RUN_PAGE_DEFAULT = 20
RUN_PAGE_MAX = 100
EVENT_PAGE_DEFAULT = 50
EVENT_PAGE_MAX = 200
# The export reads at most this many runs and events; a longer history says it was cut.
EXPORT_MAX_RUNS = 500
EXPORT_MAX_EVENTS = 2000
CREATORS_MAX = 200

ACTIVE_STATUSES = frozenset(
    {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}
)
TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})
RETRYABLE_STATUSES = frozenset({TaskStatus.FAILED, TaskStatus.CANCELLED})


@dataclass(frozen=True)
class TaskFilters:
    """``GET /api/v1/tasks`` filters. Empty ``statuses`` means every status.

    ``archived`` False lists the active list (no archived tasks), True only archived ones.
    ``created_after`` is inclusive and ``created_before`` exclusive.
    """

    statuses: frozenset[TaskStatus] = field(default_factory=frozenset)
    query: str | None = None
    created_by: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    archived: bool = False

    def without_status(self) -> "TaskFilters":
        return TaskFilters(
            query=self.query,
            created_by=self.created_by,
            created_after=self.created_after,
            created_before=self.created_before,
            archived=self.archived,
        )

    def with_archived(self, archived: bool) -> "TaskFilters":
        return TaskFilters(
            statuses=self.statuses,
            query=self.query,
            created_by=self.created_by,
            created_after=self.created_after,
            created_before=self.created_before,
            archived=archived,
        )

    def matches(self, task: Task) -> bool:
        if (task.archived_at is not None) != self.archived:
            return False
        if self.statuses and task.status not in self.statuses:
            return False
        if self.created_by is not None and task.created_by != self.created_by:
            return False
        if self.created_after is not None and task.created_at < self.created_after:
            return False
        if self.created_before is not None and task.created_at >= self.created_before:
            return False
        if self.query:
            needle = self.query.casefold()
            return any(needle in value.casefold() for value in (task.title, task.prompt, task.id))
        return True


def normalize_query(value: str | None) -> str | None:
    """The search text, trimmed; blank means no search."""
    if value is None:
        return None
    value = value.strip()
    return value[:SEARCH_MAX_CHARS] or None


def like_pattern(query: str) -> str:
    """A ``LIKE ... ESCAPE '\\'`` pattern matching ``query`` literally anywhere."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def is_task_creator(task: Task, user_id: str) -> bool:
    return task.created_by is not None and task.created_by == user_id


def redacted_checkpoint(run: AgentRun) -> dict[str, Any]:
    """The run's checkpoint with the planned tool call's secret-looking arguments redacted."""
    checkpoint = run.checkpoint.model_dump(mode="json")
    call = checkpoint.get("tool_call")
    if isinstance(call, dict) and isinstance(call.get("arguments"), dict):
        checkpoint["tool_call"] = {**call, "arguments": display_arguments(call["arguments"])}
    return checkpoint


def export_document(
    task: Task,
    runs: list[AgentRun],
    approvals: list[Approval],
    events: list[DomainEvent],
    *,
    exported_at: datetime,
    exported_by: str,
    runs_truncated: bool,
    events_truncated: bool,
) -> dict[str, Any]:
    """The task's run log as one JSON document: runs oldest first, each with its steps."""
    return {
        "format": "anum.task-run-log/v1",
        "exported_at": exported_at.isoformat(),
        "exported_by": exported_by,
        "task": task.model_dump(mode="json"),
        "runs": [
            {**run.model_dump(mode="json"), "checkpoint": redacted_checkpoint(run)} for run in runs
        ],
        "approvals": [approval.model_dump(mode="json") for approval in approvals],
        "events": [event.model_dump(mode="json") for event in events],
        "truncated": {"runs": runs_truncated, "events": events_truncated},
    }


def _fence(text: str) -> str:
    """A code fence longer than any backtick run inside ``text``."""
    longest, current = 0, 0
    for char in text:
        current = current + 1 if char == "`" else 0
        longest = max(longest, current)
    return "`" * max(3, longest + 1)


def _block(text: str) -> list[str]:
    fence = _fence(text)
    return [fence + "text", text, fence]


def export_markdown(document: dict[str, Any]) -> str:
    """A readable rendering of :func:`export_document`. Every value from the model or a
    user sits in a fenced block or a code span, so it cannot inject Markdown structure."""
    task = document["task"]
    lines = [
        f"# Task run log: {json.dumps(task['title'], ensure_ascii=False)}",
        "",
        f"- Task: `{task['id']}`",
        f"- Status: {task['status']}",
        f"- Created: {task['created_at']} by `{task.get('created_by') or 'unknown'}`",
        f"- Updated: {task['updated_at']}",
    ]
    if task.get("archived_at"):
        lines.append(f"- Archived: {task['archived_at']} by `{task.get('archived_by') or 'unknown'}`")
    lines += [
        f"- Exported: {document['exported_at']} by `{document['exported_by']}`",
        "",
        "## Instructions",
        "",
        *_block(task["prompt"]),
        "",
        f"## Runs ({len(document['runs'])})",
    ]
    if document["truncated"]["runs"]:
        lines += ["", f"Only the newest {EXPORT_MAX_RUNS} runs are included."]
    if not document["runs"]:
        lines += ["", "This task never ran."]
    for number, run in enumerate(document["runs"], start=1):
        lines += [
            "",
            f"### Run {number}: `{run['id']}`",
            "",
            f"- Status: {run['status']} (phase {run['checkpoint'].get('phase', 'unknown')})",
            f"- Started: {run['created_at']}",
            f"- Updated: {run['updated_at']}",
        ]
        for index, step in enumerate(run["steps"], start=1):
            lines += ["", f"{index}. **{step['type'].replace('_', ' ')}** at {step['created_at']}", ""]
            lines += ["   " + line if line else line for line in _block(step["summary"])]
        if run.get("result"):
            lines += ["", "Result:", "", *_block(run["result"])]
    lines += ["", f"## Approvals ({len(document['approvals'])})"]
    if not document["approvals"]:
        lines += ["", "No approvals were requested."]
    for approval in document["approvals"]:
        decided = (
            f", decided {approval['decided_at']} by `{approval.get('decided_by') or 'unknown'}`"
            if approval.get("decided_at")
            else ""
        )
        lines += [
            "",
            f"- `{approval['action']}` ({approval['risk_level']} risk): {approval['status']}{decided}",
            f"  - Approval `{approval['id']}`, run `{approval.get('run_id') or 'unknown'}`",
        ]
        if approval.get("decision_reason"):
            lines.append(f"  - Reason: `{json.dumps(approval['decision_reason'], ensure_ascii=False)}`")
    lines += ["", f"## Events ({len(document['events'])})", ""]
    if document["truncated"]["events"]:
        lines += [f"Only the newest {EXPORT_MAX_EVENTS} events are included.", ""]
    if not document["events"]:
        lines.append("No events recorded.")
    for event in document["events"]:
        lines.append(f"- {event['created_at']} `{event['type']}` on `{event['subject']}`")
    return "\n".join(lines) + "\n"
