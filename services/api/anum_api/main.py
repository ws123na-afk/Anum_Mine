from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .authorization import Permission
from .dependencies import (
    memory_repository,
    memory_repository_context,
    repository_context,
    require_permission,
    tenant_context,
)
from .errors import register_exception_handlers
from .events import CanonicalEventName, create_event
from .model_gateway import MockModelGateway
from .memory import (
    MemoryAccess,
    MemoryCreate,
    MemoryListFilters,
    MemoryNote,
    MemoryRepository,
    MemoryService,
)
from .repository import AnumRepository
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
from .request_context import CORRELATION_ID_HEADER, CorrelationIdMiddleware

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "content-type",
        "x-tenant-id",
        "x-workspace-id",
        "x-user-id",
        "x-user-roles",
        CORRELATION_ID_HEADER,
    ],
    expose_headers=[CORRELATION_ID_HEADER],
)
register_exception_handlers(app)
repository = memory_repository


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.post("/api/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Task:
    require_permission(context, Permission.TASK_CREATE)
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
        create_event(
            CanonicalEventName.TASK_CREATED,
            context,
            task.id,
            {"title": task.title},
            correlation_id=task.id,
            created_at=now,
        ).event
    )
    return task


@app.get("/api/v1/tasks/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Task:
    require_permission(context, Permission.TASK_READ)
    return _get_task_for_context(task_id, context, repository)


@app.post("/api/v1/tasks/{task_id}/run", response_model=RunTaskResponse)
async def run_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> RunTaskResponse:
    require_permission(context, Permission.TASK_RUN)
    task = _get_task_for_context(task_id, context, repository, for_update=True)
    if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be run from current state")

    runtime = AgentRuntime(MockModelGateway(), repository)
    run, approval = await runtime.run_task(task, context)
    repository.save_task(task)
    repository.save_run(run)
    return RunTaskResponse(task=task, run=run, approval=approval)


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=Task)
async def cancel_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Task:
    require_permission(context, Permission.TASK_CANCEL)
    task = _get_task_for_context(task_id, context, repository, for_update=True)
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be cancelled")

    task.status = TaskStatus.CANCELLED
    task.updated_at = utc_now()
    repository.save_task(task)

    run = repository.find_run_for_task(task.id, context)
    if run and run.status == TaskStatus.WAITING_APPROVAL:
        run.status = TaskStatus.CANCELLED
        run.updated_at = task.updated_at
        repository.save_run(run)

    for approval in repository.list_approvals_for_update(context):
        if approval.task_id == task.id and approval.status == ApprovalStatus.PENDING:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = task.updated_at
            repository.save_approval(approval)
    repository.record_event(
        create_event(
            CanonicalEventName.TASK_CANCELLED,
            context,
            task.id,
            {"task_id": task.id},
            correlation_id=task.id,
        ).event
    )
    return task


@app.get("/api/v1/agent-runs/{run_id}", response_model=AgentRun)
async def get_agent_run(
    run_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AgentRun:
    require_permission(context, Permission.TASK_READ)
    run = repository.get_run(run_id, context)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


@app.get("/api/v1/events", response_model=list[DomainEvent])
async def list_events(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[DomainEvent]:
    require_permission(context, Permission.EVENT_READ)
    return repository.list_events(context)


@app.get("/api/v1/approvals", response_model=list[Approval])
async def list_approvals(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Approval]:
    require_permission(context, Permission.APPROVAL_READ)
    return repository.list_approvals(context)


@app.post("/api/v1/memories", response_model=MemoryNote, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    memories: MemoryRepository = Depends(memory_repository_context),
) -> MemoryNote:
    require_permission(context, Permission.MEMORY_CREATE)
    if repository.get_task(payload.task_id, context) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    try:
        return MemoryService(memories).create(context, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@app.get("/api/v1/memories", response_model=list[MemoryNote])
async def list_memories(
    task_id: str | None = None,
    query: str | None = None,
    source_type: list[str] = Query(default_factory=list),
    include_expired: bool = False,
    context: TenantContext = Depends(tenant_context),
    memories: MemoryRepository = Depends(memory_repository_context),
) -> list[MemoryNote]:
    require_permission(context, Permission.MEMORY_READ)
    return MemoryService(memories).list(
        context,
        MemoryAccess(can_read_all_workspace_tasks=True),
        MemoryListFilters(
            task_id=task_id,
            query=query,
            source_types=set(source_type),
            include_expired=include_expired,
        ),
    )


@app.delete("/api/v1/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    context: TenantContext = Depends(tenant_context),
    memories: MemoryRepository = Depends(memory_repository_context),
) -> Response:
    require_permission(context, Permission.MEMORY_DELETE)
    deleted = MemoryService(memories).delete(
        context,
        memory_id,
        MemoryAccess(
            can_read_all_workspace_tasks=True,
            can_delete_any="owner" in {role.lower() for role in context.roles},
        ),
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/v1/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ApprovalDecisionResponse:
    require_permission(context, Permission.APPROVAL_DECIDE)
    return await _decide_approval(approval_id, ApprovalStatus.APPROVED, context, repository)


@app.post("/api/v1/approvals/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ApprovalDecisionResponse:
    require_permission(context, Permission.APPROVAL_DECIDE)
    return await _decide_approval(approval_id, ApprovalStatus.REJECTED, context, repository)


def _get_task_for_context(
    task_id: str,
    context: TenantContext,
    repository: AnumRepository,
    *,
    for_update: bool = False,
) -> Task:
    task = (
        repository.get_task_for_update(task_id, context)
        if for_update
        else repository.get_task(task_id, context)
    )
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


async def _decide_approval(
    approval_id: str,
    decision: ApprovalStatus,
    context: TenantContext,
    repository: AnumRepository,
) -> ApprovalDecisionResponse:
    approval = repository.get_approval(approval_id, context)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    task = _get_task_for_context(
        approval.task_id,
        context,
        repository,
        for_update=True,
    )
    approval = repository.get_approval_for_update(approval_id, context)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")

    approval.status = decision
    approval.decided_at = utc_now()
    repository.save_approval(approval)
    run = repository.find_run_for_task(task.id, context)
    repository.record_event(
        create_event(
            CanonicalEventName(f"approval.{decision.value}"),
            context,
            approval.id,
            {"task_id": task.id},
            correlation_id=task.id,
            created_at=approval.decided_at,
        ).event
    )
    runtime = AgentRuntime(MockModelGateway(), repository)
    resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
    repository.save_task(task)
    if resumed_run:
        repository.save_run(resumed_run)
    return ApprovalDecisionResponse(approval=approval, task=task, run=resumed_run)
