import asyncio

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .authorization import Permission
from .dependencies import (
    memory_repository,
    memory_repository_context,
    repository_context,
    provisioning_repository_context,
    require_permission,
    tenant_context,
)
from .errors import register_exception_handlers
from .events import CanonicalEventName, create_event
from .integrations import IntegrationConfiguration, IntegrationConfigurationView, IntegrationHealth, default_integration_registry
from .integration_tools import configured_external_handler
from .model_gateway import build_model_gateway
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
from .agent_tools import default_tool_registry
from .schemas import (
    AgentRun,
    Approval,
    ApprovalDecisionResponse,
    ApprovalStatus,
    DomainEvent,
    RunTaskResponse,
    RunPhase,
    Task,
    TaskCreate,
    TaskStatus,
    Tenant,
    TenantCreate,
    TenantContext,
    Workspace,
    WorkspaceCreate,
    WorkspaceMembership,
    new_id,
    utc_now,
)
from .settings import settings
from .store import store
from .request_context import CORRELATION_ID_HEADER, CorrelationIdMiddleware
from .voice import router as voice_router
from .phase5 import router as phase5_router
from .governance import router as governance_router
from .automation import router as automation_router
from .files import router as files_router
from .skills_api import router as skills_router
from .onboarding import router as onboarding_router

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "content-type",
        "authorization",
        "x-tenant-id",
        "x-workspace-id",
        "x-user-id",
        "x-user-roles",
        "last-event-id",
        "idempotency-key",
        CORRELATION_ID_HEADER,
    ],
    expose_headers=[CORRELATION_ID_HEADER],
)
register_exception_handlers(app)
app.include_router(voice_router)
app.include_router(phase5_router)
app.include_router(governance_router)
app.include_router(automation_router)
app.include_router(files_router)
app.include_router(skills_router)
app.include_router(onboarding_router)
repository = memory_repository
model_gateway = build_model_gateway(
    settings.model_provider,
    api_key=settings.model_api_key,
    model=settings.model_name,
    base_url=settings.model_base_url,
)
integration_registry = default_integration_registry(settings)
tool_registry = default_tool_registry(configured_external_handler(settings))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.post("/api/v1/tenants", response_model=Tenant, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> Tenant:
    require_permission(context, Permission.TENANT_CREATE)
    now = utc_now()
    tenant = Tenant(
        id=context.tenant_id,
        name=payload.name,
        created_at=now,
        updated_at=now,
    )
    try:
        return repository.create_tenant(tenant)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.post("/api/v1/workspaces", response_model=Workspace, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> Workspace:
    require_permission(context, Permission.WORKSPACE_CREATE)
    now = utc_now()
    workspace = Workspace(
        id=context.workspace_id,
        tenant_id=context.tenant_id,
        name=payload.name,
        created_at=now,
        updated_at=now,
    )
    try:
        return repository.create_workspace(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.post(
    "/api/v1/workspace-memberships/current",
    response_model=WorkspaceMembership,
    status_code=status.HTTP_201_CREATED,
)
async def create_current_membership(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> WorkspaceMembership:
    require_permission(context, Permission.MEMBERSHIP_MANAGE)
    if repository.get_workspace(context.workspace_id, context) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    role = next((role for role in ("owner", "member", "viewer") if role in context.roles), "viewer")
    now = utc_now()
    return repository.save_membership(
        WorkspaceMembership(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
    )


@app.get("/api/v1/workspaces/current", response_model=Workspace)
async def get_current_workspace(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Workspace:
    require_permission(context, Permission.TASK_READ)
    workspace = repository.get_workspace(context.workspace_id, context)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@app.get("/api/v1/workspace-memberships/current", response_model=WorkspaceMembership)
async def get_current_membership(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspaceMembership:
    require_permission(context, Permission.TASK_READ)
    membership = repository.get_membership(context)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    return membership


@app.get("/api/v1/integrations", response_model=list[IntegrationHealth])
async def list_integrations(
    context: TenantContext = Depends(tenant_context),
) -> list[IntegrationHealth]:
    require_permission(context, Permission.INTEGRATION_READ)
    return await integration_registry.health(context)


@app.get("/api/v1/integrations/{integration_id}/configuration", response_model=IntegrationConfigurationView)
async def get_integration_configuration(integration_id: str, context: TenantContext = Depends(tenant_context)) -> IntegrationConfigurationView:
    require_permission(context, Permission.INTEGRATION_READ)
    try:
        return integration_registry.configuration(integration_id, context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Integration not found") from exc


@app.put("/api/v1/integrations/{integration_id}/configuration", response_model=IntegrationConfigurationView)
async def configure_integration(integration_id: str, payload: IntegrationConfiguration, context: TenantContext = Depends(tenant_context)) -> IntegrationConfigurationView:
    require_permission(context, Permission.ORGANIZATION_MANAGE)
    try:
        return integration_registry.configure(integration_id, context, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Integration not found") from exc


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


@app.get("/api/v1/tasks", response_model=list[Task])
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Task]:
    require_permission(context, Permission.TASK_READ)
    tasks = repository.list_tasks(context)
    if cursor:
        cursor_index = next((index for index, task in enumerate(tasks) if task.id == cursor), None)
        tasks = tasks[cursor_index + 1 :] if cursor_index is not None else []
    return tasks[:limit]


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

    runtime = AgentRuntime(model_gateway, repository, tools=tool_registry)
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
    if run and run.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        run.status = TaskStatus.CANCELLED
        run.checkpoint.phase = RunPhase.CANCELLED
        run.checkpoint.version += 1
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


@app.get("/api/v1/tasks/{task_id}/latest-run", response_model=AgentRun)
async def get_latest_task_run(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AgentRun:
    require_permission(context, Permission.TASK_READ)
    _get_task_for_context(task_id, context, repository)
    run = repository.find_run_for_task(task_id, context)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


@app.post("/api/v1/agent-runs/{run_id}/resume", response_model=RunTaskResponse)
async def resume_agent_run(
    run_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> RunTaskResponse:
    require_permission(context, Permission.TASK_RUN)
    run = repository.get_run(run_id, context)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    task = _get_task_for_context(run.task_id, context, repository, for_update=True)
    runtime = AgentRuntime(model_gateway, repository, tools=tool_registry)
    try:
        resumed = await runtime.resume_run(task, run, context)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    repository.save_task(task)
    repository.save_run(resumed)
    approval = (
        repository.get_approval(resumed.checkpoint.approval_id, context)
        if resumed.checkpoint.approval_id
        else None
    )
    return RunTaskResponse(task=task, run=resumed, approval=approval)


@app.get("/api/v1/events", response_model=list[DomainEvent])
async def list_events(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[DomainEvent]:
    require_permission(context, Permission.EVENT_READ)
    return repository.list_events(context)


@app.get("/api/v1/events/stream")
async def stream_events(
    request: Request,
    task_id: str | None = None,
    follow: bool = True,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> StreamingResponse:
    require_permission(context, Permission.EVENT_READ)

    async def event_source():
        cursor = last_event_id
        idle_cycles = 0
        while not await request.is_disconnected():
            events = repository.list_events(context)
            if task_id:
                events = [
                    event
                    for event in events
                    if event.subject == task_id or event.payload.get("task_id") == task_id
                ]
            if cursor:
                cursor_index = next(
                    (index for index, event in enumerate(events) if event.id == cursor),
                    None,
                )
                events = events[cursor_index + 1 :] if cursor_index is not None else events

            if events:
                idle_cycles = 0
                for event in events:
                    cursor = event.id
                    yield (
                        f"id: {event.id}\n"
                        f"event: {event.type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
            elif not follow:
                break
            else:
                idle_cycles += 1
                if idle_cycles >= 15:
                    yield ": keep-alive\n\n"
                    idle_cycles = 0
            if not follow:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/approvals", response_model=list[Approval])
async def list_approvals(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Approval]:
    require_permission(context, Permission.APPROVAL_READ)
    return repository.list_approvals(context)


@app.get("/api/v1/approvals/{approval_id}", response_model=Approval)
async def get_approval(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Approval:
    require_permission(context, Permission.APPROVAL_READ)
    approval = repository.get_approval(approval_id, context)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return approval


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
    runtime = AgentRuntime(model_gateway, repository, tools=tool_registry)
    resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
    repository.save_task(task)
    if resumed_run:
        repository.save_run(resumed_run)
    return ApprovalDecisionResponse(approval=approval, task=task, run=resumed_run)
