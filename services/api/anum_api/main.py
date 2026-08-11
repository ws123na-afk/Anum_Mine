from fastapi import Depends, FastAPI, HTTPException, status

from .dependencies import tenant_context
from .model_gateway import MockModelGateway
from .runtime import AgentRuntime
from .schemas import (
    AgentRun,
    Approval,
    ApprovalDecisionResponse,
    ApprovalStatus,
    DomainEvent,
    RunTaskResponse,
    Task,
    TaskCreate,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from .settings import settings
from .store import store

app = FastAPI(title=settings.app_name, version="0.1.0")
runtime = AgentRuntime(MockModelGateway(), store)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.post("/api/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    context: TenantContext = Depends(tenant_context),
) -> Task:
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=payload.title,
        prompt=payload.prompt,
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
    )
    store.tasks[task.id] = task
    store.events.append(
        DomainEvent(
            id=new_id("event"),
            type="task.created",
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            subject=task.id,
            correlation_id=new_id("correlation"),
            created_at=now,
            payload={"title": task.title},
        )
    )
    return task


@app.get("/api/v1/tasks/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
) -> Task:
    task = _get_task_for_context(task_id, context)
    return task


@app.post("/api/v1/tasks/{task_id}/run", response_model=RunTaskResponse)
async def run_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
) -> RunTaskResponse:
    task = _get_task_for_context(task_id, context)
    if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be run from current state")

    run, approval = await runtime.run_task(task, context)
    store.runs[run.id] = run
    return RunTaskResponse(task=task, run=run, approval=approval)


@app.get("/api/v1/agent-runs/{run_id}", response_model=AgentRun)
async def get_agent_run(
    run_id: str,
    context: TenantContext = Depends(tenant_context),
) -> AgentRun:
    run = store.runs.get(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    _get_task_for_context(run.task_id, context)
    return run


@app.get("/api/v1/events", response_model=list[DomainEvent])
async def list_events(context: TenantContext = Depends(tenant_context)) -> list[DomainEvent]:
    return [event for event in store.events if event.tenant_id == context.tenant_id]


@app.get("/api/v1/approvals", response_model=list[Approval])
async def list_approvals(context: TenantContext = Depends(tenant_context)) -> list[Approval]:
    return [
        approval
        for approval in store.approvals.values()
        if _approval_belongs_to_context(approval.task_id, context)
    ]


@app.post("/api/v1/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
) -> ApprovalDecisionResponse:
    return await _decide_approval(approval_id, ApprovalStatus.APPROVED, context)


@app.post("/api/v1/approvals/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
) -> ApprovalDecisionResponse:
    return await _decide_approval(approval_id, ApprovalStatus.REJECTED, context)


def _get_task_for_context(task_id: str, context: TenantContext) -> Task:
    task = store.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.tenant_id != context.tenant_id or task.workspace_id != context.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _approval_belongs_to_context(task_id: str, context: TenantContext) -> bool:
    try:
        _get_task_for_context(task_id, context)
    except HTTPException:
        return False
    return True


async def _decide_approval(
    approval_id: str,
    decision: ApprovalStatus,
    context: TenantContext,
) -> ApprovalDecisionResponse:
    approval = store.approvals.get(approval_id)
    if not approval or not _approval_belongs_to_context(approval.task_id, context):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")

    approval.status = decision
    approval.decided_at = utc_now()
    task = _get_task_for_context(approval.task_id, context)
    run = next((item for item in store.runs.values() if item.task_id == task.id), None)
    resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
    return ApprovalDecisionResponse(approval=approval, task=task, run=resumed_run)
