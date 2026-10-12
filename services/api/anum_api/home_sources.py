"""Built-in Home sources (docs/home.md#sources): Today's Focus, insights and activity.

Each source reads one module through its own API (repository, store or engine) inside the
caller's tenant and workspace, so RLS and the module's own permission still apply. Tasks
have no due date, so "overdue" means a task that failed and was not retried, or a run
that has not moved for longer than ``STALLED_AFTER``.

Importing this module registers the sources; ``anum_api.home`` imports it on first use.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .approval_integrity import as_viewed
from .authorization import Permission
from .dependencies import has_permission
from .home import (
    ActivityItem,
    FocusItem,
    FocusResult,
    FocusSource,
    HomeScope,
    Insight,
    InsightAction,
    InsightSource,
    SourceNotConnected,
    SourceNotInstalled,
    Target,
    TodayProgress,
    register_focus_source,
    register_insight_source,
)
from .schemas import ApprovalStatus, RiskLevel, RunPhase, Task, TaskStatus
from .tasks import TaskFilters

STALLED_AFTER = timedelta(hours=2)
APPROVAL_WAITING_AFTER = timedelta(hours=24)
FAILED_WITHIN = timedelta(days=7)
IN_PROGRESS = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL})
# How far along a run is, by its checkpoint stage (the runtime records no percentage).
PHASE_PROGRESS = {
    RunPhase.PLANNING: 0.2,
    RunPhase.TOOL_READY: 0.4,
    RunPhase.WAITING_APPROVAL: 0.5,
    RunPhase.EXECUTING: 0.75,
    RunPhase.COMPLETED: 1.0,
}
RECENT_TASKS = 50


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _recent_tasks(scope: HomeScope) -> list[Task]:
    return scope.repository.list_tasks(scope.context, limit=RECENT_TASKS, filters=TaskFilters())


def _pending_approvals(scope: HomeScope) -> list:  # type: ignore[type-arg]
    viewed = [as_viewed(a, scope.now) for a in scope.repository.list_approvals(scope.context)]
    return sorted((a for a in viewed if a.status == ApprovalStatus.PENDING), key=lambda a: a.created_at)


# -- Today's Focus --------------------------------------------------------------------------------


def focus_approvals(scope: HomeScope) -> FocusResult:
    pending = _pending_approvals(scope)
    if not pending:
        return FocusResult(items=[], count=0)
    high = any(a.risk_level in (RiskLevel.HIGH, RiskLevel.BLOCKED) for a in pending)
    oldest = pending[0]
    item = FocusItem(
        id="approvals",
        source="approvals",
        kind="approvals_pending",
        title=f"{len(pending)} approval{'s' if len(pending) != 1 else ''} waiting",
        detail="Needs your decision",
        count=len(pending),
        severity="high" if high else "medium",
        at=oldest.created_at,
        target=Target(feature="approvals", id=oldest.id if len(pending) == 1 else None),
        params={"count": len(pending)},
    )
    return FocusResult(items=[item], count=len(pending))


def focus_email(scope: HomeScope) -> FocusResult:
    from .email_api import summary_for

    summary = summary_for(scope.context)
    if summary.accounts == 0:
        raise SourceNotConnected("No mailbox is connected in this workspace.")
    urgent = summary.high_priority_unread
    if urgent == 0:
        return FocusResult(items=[], count=0)
    newest = summary.needs_attention[0] if summary.needs_attention else None
    item = FocusItem(
        id="email",
        source="email",
        kind="email_urgent",
        title=f"{urgent} urgent email{'s' if urgent != 1 else ''}",
        detail="Needs your reply",
        count=urgent,
        severity="high",
        at=datetime.fromisoformat(newest.received_at) if newest else None,
        target=Target(feature="email"),
        params={"count": urgent},
    )
    return FocusResult(items=[item], count=urgent)


def focus_tasks(scope: HomeScope) -> FocusResult:
    tasks = [t for t in _recent_tasks(scope) if t.status in IN_PROGRESS]
    tasks.sort(key=lambda t: t.updated_at, reverse=True)
    items: list[FocusItem] = []
    for task in tasks[:3]:
        run = scope.repository.find_run_for_task(task.id, scope.context)
        phase = run.checkpoint.phase if run is not None else None
        items.append(
            FocusItem(
                id=f"task:{task.id}",
                source="tasks",
                kind="task_in_progress",
                title=task.title,
                detail=task.status.value,
                progress=PHASE_PROGRESS.get(phase) if phase is not None else None,
                severity="medium" if task.status == TaskStatus.WAITING_APPROVAL else "low",
                at=task.updated_at,
                target=Target(feature="tasks", id=task.id),
                params={"status": task.status.value, "phase": phase.value if phase else None},
            )
        )
    return FocusResult(items=items, count=len(tasks))


def focus_automations(scope: HomeScope) -> FocusResult:
    from .automation import RunFilter, RunStatus, current_engine

    runs, _ = current_engine().page_runs(scope.context, RunFilter(status=RunStatus.FAILED, limit=50))
    recent = [r for r in runs if r.created_at >= scope.now - timedelta(hours=24)]
    if not recent:
        return FocusResult(items=[], count=0)
    item = FocusItem(
        id="automations",
        source="automations",
        kind="automations_failed",
        title=f"{len(recent)} automation run{'s' if len(recent) != 1 else ''} failed today",
        detail=recent[0].workflow_name,
        count=len(recent),
        severity="high",
        at=recent[0].created_at,
        target=Target(feature="automations", id=recent[0].workflow_id if len(recent) == 1 else None),
        params={"count": len(recent)},
    )
    return FocusResult(items=[item], count=len(recent))


# -- summary parts that are not sources ------------------------------------------------------------


def today_progress(scope: HomeScope) -> TodayProgress:
    """Tasks completed since midnight (UTC) against the ones still open."""
    start = _start_of_day(scope.now)
    tasks = _recent_tasks(scope)
    completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED and t.updated_at >= start)
    open_tasks = sum(1 for t in tasks if t.status in IN_PROGRESS or t.status == TaskStatus.CREATED)
    return TodayProgress(completed=completed, open=open_tasks)


def recent_activity(scope: HomeScope) -> list[ActivityItem]:
    """The newest task changes and automation runs the caller may read."""
    items: list[ActivityItem] = []
    if has_permission(scope.context, Permission.TASK_READ):
        for task in _recent_tasks(scope):
            items.append(
                ActivityItem(
                    id=f"task:{task.id}", kind="task", title=task.title, status=task.status.value,
                    at=task.updated_at, target=Target(feature="tasks", id=task.id),
                )
            )
    if has_permission(scope.context, Permission.AUTOMATION_READ):
        try:
            from .automation import RunFilter, current_engine

            runs, _ = current_engine().page_runs(scope.context, RunFilter(limit=10))
        except Exception:  # automations are optional for Home
            runs = []
        for run in runs:
            items.append(
                ActivityItem(
                    id=f"automation_run:{run.id}", kind="automation_run", title=run.workflow_name or run.workflow_id,
                    status=run.status.value, at=run.updated_at, target=Target(feature="automations", id=run.workflow_id),
                )
            )
    items.sort(key=lambda i: i.at, reverse=True)
    return items


# -- insights ----------------------------------------------------------------------------------------


def insights_autonomy(scope: HomeScope) -> list[Insight]:
    from .autonomy import ProposalStatus, store_of

    store = store_of(scope.repository)
    if store is None:
        raise SourceNotInstalled("Autonomy storage is not available.")
    insights = []
    for proposal in store.list_proposals(scope.context, ProposalStatus.PENDING):
        insights.append(
            Insight(
                key=f"autonomy:{proposal.id}",
                source="autonomy",
                kind="autonomy_proposal",
                severity="low",
                title="You approve this every time",
                finding=f"You approved {proposal.tool} {proposal.approvals} times in a row.",
                question="Shall ANUM do it without asking and tell you afterwards?",
                params={"tool": proposal.tool, "approvals": proposal.approvals},
                action=InsightAction(type="open", verb="review", target=Target(feature="autonomy", id=proposal.id)),
                created_at=proposal.last_approved_at or proposal.created_at,
            )
        )
    return insights


def insights_approvals(scope: HomeScope) -> list[Insight]:
    insights = []
    for approval in _pending_approvals(scope):
        if scope.now - approval.created_at < APPROVAL_WAITING_AFTER:
            continue
        hours = int((scope.now - approval.created_at).total_seconds() // 3600)
        insights.append(
            Insight(
                key=f"approvals:{approval.id}",
                source="approvals",
                kind="approval_waiting",
                severity="high" if approval.risk_level in (RiskLevel.HIGH, RiskLevel.BLOCKED) else "medium",
                title="An approval is waiting",
                finding=f"{approval.action} has waited {hours} hours for a decision.",
                question="Shall I open it so you can decide?",
                params={"action": approval.action, "hours": hours},
                action=InsightAction(type="open", verb="review", target=Target(feature="approvals", id=approval.id)),
                created_at=approval.created_at,
            )
        )
    return insights


def insights_tasks(scope: HomeScope) -> list[Insight]:
    insights = []
    for task in _recent_tasks(scope):
        if task.status == TaskStatus.FAILED and task.updated_at >= scope.now - FAILED_WITHIN:
            insights.append(
                Insight(
                    key=f"tasks:failed:{task.id}",
                    source="tasks",
                    kind="task_failed",
                    severity="high",
                    title="A task failed",
                    finding=f"“{task.title}” failed and was not retried.",
                    question="Shall I analyze what went wrong?",
                    params={"title": task.title},
                    action=InsightAction(
                        type="command", verb="analyze", target=Target(feature="tasks", id=task.id),
                        command=f"Analyze why the task “{task.title}” failed and suggest how to fix it.",
                    ),
                    created_at=task.updated_at,
                )
            )
        elif task.status in (TaskStatus.QUEUED, TaskStatus.RUNNING) and scope.now - task.updated_at >= STALLED_AFTER:
            hours = int((scope.now - task.updated_at).total_seconds() // 3600)
            insights.append(
                Insight(
                    key=f"tasks:stalled:{task.id}",
                    source="tasks",
                    kind="task_stalled",
                    severity="medium",
                    title="A task has not moved",
                    finding=f"“{task.title}” has been {task.status.value} for {hours} hours.",
                    question="Shall I check on it now?",
                    params={"title": task.title, "hours": hours, "status": task.status.value},
                    action=InsightAction(type="open", verb="check_now", target=Target(feature="tasks", id=task.id)),
                    created_at=task.updated_at,
                )
            )
    return insights


def insights_automations(scope: HomeScope) -> list[Insight]:
    from .automation import WorkflowFilter, WorkflowStatus, current_engine
    from .automation import RunStatus as AutomationRunStatus

    workflows, _ = current_engine().page_workflows(scope.context, WorkflowFilter(status=WorkflowStatus.ACTIVE, limit=200))
    insights = []
    for workflow in workflows:
        if workflow.last_run_status != AutomationRunStatus.FAILED or workflow.last_run_at is None:
            continue
        insights.append(
            Insight(
                # The last failure's time is part of the key: a new failure comes back after a dismiss.
                key=f"automations:{workflow.id}:{int(workflow.last_run_at.timestamp())}",
                source="automations",
                kind="automation_failing",
                severity="high",
                title="An automation is failing",
                finding=f"The last run of “{workflow.name}” failed.",
                question="Shall I analyze the failure and suggest a fix?",
                params={"name": workflow.name},
                action=InsightAction(
                    type="command", verb="analyze", target=Target(feature="automations", id=workflow.id),
                    command=f"Analyze why the automation “{workflow.name}” failed on its last run and suggest a fix.",
                ),
                created_at=workflow.last_run_at,
            )
        )
    return insights


def insights_integrations(scope: HomeScope) -> list[Insight]:
    from .integrations import IntegrationStatus

    integrations = getattr(scope.state, "integration_registry", None)
    if integrations is None:
        raise SourceNotInstalled("The integration registry is not configured.")
    insights = []
    for definition, check in integrations.recorded_checks(scope.context):
        if check.status != IntegrationStatus.DEGRADED:
            continue
        insights.append(
            Insight(
                key=f"integrations:{definition.id}:{int(check.checked_at.timestamp())}",
                source="integrations",
                kind="integration_degraded",
                severity="medium",
                title="A connection has a problem",
                finding=f"{definition.name} did not answer its last health check.",
                question="Shall I check it now?",
                params={"name": definition.name},
                action=InsightAction(type="open", verb="check_now", target=Target(feature="integrations", id=definition.id)),
                created_at=check.checked_at,
            )
        )
    return insights


def insights_email(scope: HomeScope) -> list[Insight]:
    from .email_api import summary_for
    from .email_inbox import AccountStatus, open_email_store, visible_accounts

    with open_email_store(scope.context) as store:
        accounts = visible_accounts(store, scope.context)
    if not accounts:
        raise SourceNotConnected("No mailbox is connected in this workspace.")
    insights = []
    for account in accounts:
        if account.status == AccountStatus.REAUTH_REQUIRED:
            insights.append(
                Insight(
                    key=f"email:reauth:{account.id}",
                    source="email",
                    kind="mailbox_reauth",
                    severity="high",
                    title="A mailbox needs reconnecting",
                    finding=f"{account.address} stopped syncing: its sign-in expired.",
                    question="Reconnect it from the web app so new mail keeps arriving.",
                    params={"address": account.address},
                    action=InsightAction(type="open", verb="check_now", target=Target(feature="email")),
                    created_at=account.last_sync_at or account.created_at,
                )
            )
    summary = summary_for(scope.context)
    if summary.high_priority_unread and summary.needs_attention:
        newest = summary.needs_attention[0]
        count = summary.high_priority_unread
        insights.append(
            Insight(
                key=f"email:urgent:{newest.id}",
                source="email",
                kind="email_urgent",
                severity="high",
                title="Urgent emails",
                finding=f"{count} high-priority email{'s' if count != 1 else ''} {'are' if count != 1 else 'is'} unread.",
                question="Shall I summarize them and draft replies for you to check?",
                params={"count": count},
                action=InsightAction(
                    type="command", verb="analyze", target=Target(feature="email"),
                    command="Summarize my unread high-priority emails and draft replies for me to review.",
                ),
                created_at=datetime.fromisoformat(newest.received_at),
            )
        )
    return insights


register_focus_source(FocusSource("approvals", focus_approvals, Permission.APPROVAL_READ, order=10))
register_focus_source(FocusSource("email", focus_email, Permission.EMAIL_READ, order=20))
register_focus_source(FocusSource("tasks", focus_tasks, Permission.TASK_READ, order=40))
register_focus_source(FocusSource("automations", focus_automations, Permission.AUTOMATION_READ, order=50))

register_insight_source(InsightSource("autonomy", insights_autonomy, Permission.POLICY_READ, order=50))
register_insight_source(InsightSource("approvals", insights_approvals, Permission.APPROVAL_READ, order=10))
register_insight_source(InsightSource("tasks", insights_tasks, Permission.TASK_READ, order=20))
register_insight_source(InsightSource("automations", insights_automations, Permission.AUTOMATION_READ, order=30))
register_insight_source(InsightSource("integrations", insights_integrations, Permission.INTEGRATION_READ, order=40))
register_insight_source(InsightSource("email", insights_email, Permission.EMAIL_READ, order=15))
