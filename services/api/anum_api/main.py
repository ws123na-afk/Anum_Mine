from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .dependencies import tenant_context
from .model_gateway import MockModelGateway
from .repository import InMemoryRepository
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-tenant-id", "x-workspace-id", "x-user-id", "x-user-roles"],
)
repository = InMemoryRepository(store)
runtime = AgentRuntime(MockModelGateway(), repository)


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
    repository.create_task(task)
    repository.record_event(
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
    return _get_task_for_context(task_id, context)


@app.post("/api/v1/tasks/{task_id}/run", response_model=RunTaskResponse)
async def run_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
) -> RunTaskResponse:
    task = _get_task_for_context(task_id, context)
    if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be run from current state")

    run, approval = await runtime.run_task(task, context)
    repository.save_run(run)
    return RunTaskResponse(task=task, run=run, approval=approval)


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=Task)
async def cancel_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
) -> Task:
    task = _get_task_for_context(task_id, context)
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be cancelled")

    task.status = TaskStatus.CANCELLED
    task.updated_at = utc_now()
    repository.save_task(task)
    repository.record_event(
        DomainEvent(
            id=new_id("event"),
            type="task.cancelled",
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            subject=task.id,
            correlation_id=new_id("correlation"),
            created_at=utc_now(),
            payload={"task_id": task.id},
        )
    )
    return task


@app.get("/api/v1/agent-runs/{run_id}", response_model=AgentRun)
async def get_agent_run(
    run_id: str,
    context: TenantContext = Depends(tenant_context),
) -> AgentRun:
    run = repository.get_run(run_id, context)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


@app.get("/api/v1/events", response_model=list[DomainEvent])
async def list_events(context: TenantContext = Depends(tenant_context)) -> list[DomainEvent]:
    return repository.list_events(context)


@app.get("/api/v1/approvals", response_model=list[Approval])
async def list_approvals(context: TenantContext = Depends(tenant_context)) -> list[Approval]:
    return repository.list_approvals(context)


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
    task = repository.get_task(task_id, context)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


async def _decide_approval(
    approval_id: str,
    decision: ApprovalStatus,
    context: TenantContext,
) -> ApprovalDecisionResponse:
    approval = repository.get_approval(approval_id, context)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")

    approval.status = decision
    approval.decided_at = utc_now()
    repository.save_approval(approval)
    task = _get_task_for_context(approval.task_id, context)
    run = repository.find_run_for_task(task.id, context)
    resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
    if resumed_run:
        repository.save_run(resumed_run)
    return ApprovalDecisionResponse(approval=approval, task=task, run=resumed_run)
