import asyncio
from datetime import datetime
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .authorization import Permission
from .dependencies import (
    event_runtime,
    list_events_for_stream,
    memory_repository,
    memory_repository_context,
    repository_context,
    provisioning_repository_context,
    provisioning_tenant_context,
    require_permission,
    tenant_context,
)
from .approval_integrity import as_viewed
from .audit import AuditRecord
from .errors import ApplicationError, ErrorCode, application_error_handler, error_response, register_exception_handlers
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
from .realtime import live_event_source
from .runtime import AgentRuntime
from .durable_runs import build_run_dispatcher, run_input_for, workflow_id_for
from .valkey import CoordinationUnavailable, LockNotAcquired, build_run_lock_manager
from .agent_tools import default_tool_registry
from .tool_governance import decision_requirements, match_governance, required_approvals
from .schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalApproverRecord,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalRejectRequest,
    ApprovalStatus,
    RiskLevel,
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
    WorkspaceApprovalPolicy,
    WorkspaceApprovalPolicyUpdate,
    WorkspaceCreate,
    WorkspaceMembership,
    new_id,
    utc_now,
)
from .settings import settings
from .store import store
from .request_context import CORRELATION_ID_HEADER, CorrelationIdMiddleware
from .hardening import docs_routes, enforce_startup_policy, install_hardening
from .voice import router as voice_router
from .phase5 import router as phase5_router
from .governance import router as governance_router
from .automation import build_automation_scheduler, router as automation_router
from .files import router as files_router
from .skills_api import router as skills_router
from .model_budget import ModelBudgetExceededError, check_model_budget, router as model_budget_router
from .onboarding import budgeted_model_gateway, router as onboarding_router
from .workspace_members import router as workspace_members_router
from .membership_directory import router as membership_directory_router
from .identity import validate_auth_configuration
from .telemetry import HttpMetricsMiddleware, setup_telemetry, shutdown_telemetry, sqlalchemy_engines

# Fail fast: header mode and anum_local_* sessions must never serve a non-local environment.
validate_auth_configuration(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await event_runtime.start()
    # Fires due automation schedules when ANUM_AUTOMATION_SCHEDULER_ENABLED=true; safe on
    # every replica with PostgreSQL (docs/automation.md#scheduler).
    scheduler = build_automation_scheduler(settings)
    if scheduler is not None:
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        await event_runtime.stop()
        shutdown_telemetry()


enforce_startup_policy(settings)
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan, **docs_routes(settings))
app.add_middleware(CorrelationIdMiddleware)
install_hardening(
    app,
    settings,
    upload_path_prefix=files_router.prefix,
    upload_max_bytes=settings.max_upload_body_bytes,
)
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
    expose_headers=[CORRELATION_ID_HEADER, "Retry-After"],
)
# Outermost ANUM middleware, so 413/429 answers and CORS preflights are counted too.
app.add_middleware(HttpMetricsMiddleware)
# OTLP export when ANUM_OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_ENDPOINT is set;
# otherwise only log correlation (docs/observability.md).
setup_telemetry(settings, service_name="anum-api", app=app, engines=sqlalchemy_engines(event_runtime))
register_exception_handlers(app)


@app.exception_handler(ModelBudgetExceededError)
async def model_budget_exceeded_handler(request: Request, exc: ModelBudgetExceededError) -> Response:
    # 402: the tenant's or workspace's monthly model budget is used up (threat model G4).
    return await application_error_handler(
        request,
        ApplicationError(ErrorCode.MODEL_BUDGET_EXCEEDED, exc.message, status_code=status.HTTP_402_PAYMENT_REQUIRED),
    )


app.include_router(voice_router)
app.include_router(phase5_router)
app.include_router(governance_router)
app.include_router(automation_router)
app.include_router(files_router)
app.include_router(skills_router)
app.include_router(onboarding_router)
app.include_router(workspace_members_router)
app.include_router(membership_directory_router)
app.include_router(model_budget_router)
repository = memory_repository
model_gateway = build_model_gateway(
    settings.model_provider,
    api_key=settings.model_api_key,
    model=settings.model_name,
    base_url=settings.model_base_url,
)
integration_registry = default_integration_registry(settings)
tool_registry = default_tool_registry(configured_external_handler(settings))
# Stage 3 coordination (docs/agent-runtime.md): an optional Valkey lock per task and,
# with ANUM_RUNTIME_BACKEND=temporal, a dispatcher that hands runs to the worker.
run_locks = build_run_lock_manager(settings)
run_dispatcher = build_run_dispatcher(settings)


@asynccontextmanager
async def _task_lock(context: TenantContext, task_id: str) -> AsyncIterator[None]:
    """Hold the task's distributed run lock (a no-op unless ANUM_RUN_LOCK_BACKEND=valkey)."""
    try:
        lock = await run_locks.acquire(context, task_id)
    except LockNotAcquired as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task is being processed by another request",
        ) from exc
    except CoordinationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Run coordination is unavailable",
        ) from exc
    try:
        yield
    finally:
        await run_locks.release(lock)


async def _dispatch(context: TenantContext, task_id: str, run_id: str) -> None:
    if run_dispatcher is None:
        raise RuntimeError("ANUM_RUNTIME_BACKEND is not temporal")
    try:
        await run_dispatcher.start(run_input_for(context, task_id, run_id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The durable runtime is unavailable",
        ) from exc


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.post("/api/v1/tenants", response_model=Tenant, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    context: TenantContext = Depends(provisioning_tenant_context),
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
    context: TenantContext = Depends(provisioning_tenant_context),
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
    context: TenantContext = Depends(provisioning_tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> WorkspaceMembership:
    require_permission(context, Permission.MEMBERSHIP_MANAGE)
    if repository.get_workspace(context.workspace_id, context) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if repository.get_membership(context) is None and repository.workspace_has_members(context):
        # Self-service membership only bootstraps an empty workspace. Joining a
        # workspace that already has members needs an existing owner to add you.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This workspace already has members. Ask a workspace owner to add you.",
        )
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
        created_by=context.user_id,
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
    # Refuse before the task changes state when the monthly model budget is used up.
    check_model_budget(context)
    async with _task_lock(context, task_id):
        task = _get_task_for_context(task_id, context, repository, for_update=True)
        if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be run from current state")

        runtime = AgentRuntime(budgeted_model_gateway(context, model_gateway), repository, tools=tool_registry)
        if run_dispatcher is not None:
            return await _queue_durable_run(task, context, repository, runtime)
        run, approval = await runtime.run_task(task, context)
        repository.save_task(task)
        repository.save_run(run)
        return RunTaskResponse(task=task, run=run, approval=approval)


async def _queue_durable_run(
    task: Task, context: TenantContext, repository: AnumRepository, runtime: AgentRuntime
) -> RunTaskResponse:
    """Persist a queued run and start its Temporal workflow (ANUM_RUNTIME_BACKEND=temporal)."""
    if repository.find_run_for_task(task.id, context) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task already has a run")
    run = runtime.new_run(task, status=TaskStatus.QUEUED)
    workflow_id = workflow_id_for(context.tenant_id, context.workspace_id, task.id)
    run.steps.append(
        AgentRunStep(
            id=new_id("step"),
            type="queued",
            summary="Queued for a durable worker.",
            created_at=utc_now(),
            metadata={"workflow_id": workflow_id},
        )
    )
    task.status = TaskStatus.QUEUED
    task.updated_at = run.updated_at
    repository.save_task(task)
    repository.save_run(run)
    await _dispatch(context, task.id, run.id)
    return RunTaskResponse(task=task, run=run, approval=None)


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
    if run_dispatcher is not None and run is not None:
        await run_dispatcher.cancelled(context, task.id)
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
    async with _task_lock(context, run.task_id):
        task = _get_task_for_context(run.task_id, context, repository, for_update=True)
        run = repository.get_run(run_id, context) or run
        runtime = AgentRuntime(budgeted_model_gateway(context, model_gateway), repository, tools=tool_registry)
        try:
            if run_dispatcher is not None:
                # The worker resumes from the same checkpoint; this only (re)starts its workflow.
                runtime.check_resumable(task, run)
                await _dispatch(context, task.id, run.id)
                return RunTaskResponse(task=task, run=run, approval=None)
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
    sse_headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if event_runtime.live:
        return StreamingResponse(
            live_event_source(
                hub=event_runtime.hub,
                context=context,
                list_events=lambda: list_events_for_stream(context),
                is_disconnected=request.is_disconnected,
                task_id=task_id,
                follow=follow,
                last_event_id=last_event_id,
            ),
            media_type="text/event-stream",
            headers=sse_headers,
        )

    async def event_source():
        cursor = last_event_id
        idle_cycles = 0
        while not await request.is_disconnected():
            events = list_events_for_stream(context)
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
        headers=sse_headers,
    )


@app.get("/api/v1/approvals", response_model=list[Approval])
async def list_approvals(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Approval]:
    require_permission(context, Permission.APPROVAL_READ)
    now = utc_now()
    return _with_progress(
        [as_viewed(approval, now) for approval in repository.list_approvals(context)],
        context,
        repository,
    )


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
    (shown,) = _with_progress([as_viewed(approval, utc_now())], context, repository)
    return shown


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


_DECISION_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_409_CONFLICT: {"description": "Already decided, or the payload hash does not match"},
    status.HTTP_410_GONE: {"description": "The approval expired; its run was failed"},
}


@app.post(
    "/api/v1/approvals/{approval_id}/approve",
    response_model=ApprovalDecisionResponse,
    responses=_DECISION_ERRORS,
)
async def approve(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ApprovalDecisionResponse | JSONResponse:
    require_permission(context, Permission.APPROVAL_DECIDE)
    return await _decide_approval(
        approval_id,
        ApprovalStatus.APPROVED,
        payload.payload_hash,
        payload.reason,
        request,
        context,
        repository,
    )


@app.post(
    "/api/v1/approvals/{approval_id}/reject",
    response_model=ApprovalDecisionResponse,
    responses=_DECISION_ERRORS,
)
async def reject(
    approval_id: str,
    request: Request,
    payload: ApprovalRejectRequest | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ApprovalDecisionResponse | JSONResponse:
    require_permission(context, Permission.APPROVAL_DECIDE)
    return await _decide_approval(
        approval_id,
        ApprovalStatus.REJECTED,
        payload.payload_hash if payload else None,
        payload.reason if payload else None,
        request,
        context,
        repository,
    )


@app.get("/api/v1/approval-policy", response_model=WorkspaceApprovalPolicy)
async def get_approval_policy(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspaceApprovalPolicy:
    require_permission(context, Permission.APPROVAL_READ)
    return repository.get_approval_policy(context)


@app.put("/api/v1/approval-policy", response_model=WorkspaceApprovalPolicy)
async def update_approval_policy(
    payload: WorkspaceApprovalPolicyUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspaceApprovalPolicy:
    """Owner-only: set the workspace's two-person rule and medium-risk approval policy."""
    require_permission(context, Permission.POLICY_MANAGE)
    previous = repository.get_approval_policy(context)
    now = utc_now()
    saved = repository.save_approval_policy(
        WorkspaceApprovalPolicy(
            two_person_rule=payload.two_person_rule,
            medium_risk_requires_approval=payload.medium_risk_requires_approval,
            updated_by=context.user_id,
            updated_at=now,
        ),
        context,
    )
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action="approval_policy.updated",
            target=context.workspace_id,
            outcome="success",
            correlation_id=new_id("policy"),
            created_at=now,
            metadata={
                "before": {
                    "two_person_rule": previous.two_person_rule,
                    "medium_risk_requires_approval": previous.medium_risk_requires_approval,
                },
                "after": {
                    "two_person_rule": saved.two_person_rule,
                    "medium_risk_requires_approval": saved.medium_risk_requires_approval,
                },
            },
        )
    )
    return saved


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
    shown_hash: str | None,
    reason: str | None,
    request: Request,
    context: TenantContext,
    repository: AnumRepository,
) -> ApprovalDecisionResponse | JSONResponse:
    approval = repository.get_approval(approval_id, context)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    async with _task_lock(context, approval.task_id):
        return await _decide_approval_locked(
            approval_id, decision, shown_hash, reason, request, context, repository
        )


# Risk levels the optional two-person rule applies to (docs/approvals-and-risk.md, A6).
TWO_PERSON_RISK_LEVELS = frozenset({RiskLevel.HIGH})


class _DecisionRequirements:
    """What the matching organization approval rules demand of an approve decision."""

    def __init__(self, required: int = 1, rules: list[str] | None = None) -> None:
        self.required = required
        self.rules = rules or []


def _governance_decision_refusal(
    request: Request,
    context: TenantContext,
    repository: AnumRepository,
    task: Task,
    approval: Approval,
    now: datetime,
) -> tuple[JSONResponse | None, _DecisionRequirements]:
    """Enforce the matching organization approval rules on an approve (threat model A4).

    The rules are re-read now, in the caller's tenant scope, and matched against the
    approval's tool, integration target and risk level. A decider must hold a role every
    matching rule requires; with ``minimum_approvers`` 2 or more the task creator and
    the requester cannot approve; above 2 the approval needs that many distinct
    approvers (an approval chain, see ``required_approvals``). Each refusal is audited
    and leaves the approval pending. Returns the refusal (or None) and the requirements.
    """
    match = match_governance(
        repository.get_tool_governance(context),
        tool=approval.action,
        target=approval.target,
        risk_level=approval.risk_level,
    )
    if not match.approval_rules:
        return None, _DecisionRequirements()
    minimum, allowed_roles = decision_requirements(match)
    rules = [rule.name for rule in match.approval_rules]
    requirements = _DecisionRequirements(required_approvals(minimum), rules)
    roles = {role.lower() for role in context.roles}
    refusal: tuple[str, str] | None = None
    if allowed_roles is not None and not roles & allowed_roles:
        refusal = (
            "approval.role_denied",
            "An organization approval rule requires a decider with one of these roles: "
            f"{', '.join(sorted(allowed_roles)) or 'none'}. You can still reject it.",
        )
    elif minimum > 2 and context.user_id in {task.created_by, approval.requested_by}:
        refusal = (
            "approval.self_approval_denied",
            f"An organization approval rule requires {minimum} approvers other than the "
            "person who created or started this task, so you cannot approve it. You can "
            "still reject it.",
        )
    elif minimum == 2 and context.user_id in {task.created_by, approval.requested_by}:
        refusal = (
            "approval.self_approval_denied",
            "An organization approval rule requires two people: you created or started this "
            "task, so another owner must approve it. You can still reject it.",
        )
    if refusal is None:
        return None, requirements
    action, message = refusal
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=approval.id,
            outcome="denied",
            correlation_id=task.id,
            created_at=now,
            metadata={
                "task_id": task.id,
                "tool": approval.action,
                "risk_level": approval.risk_level.value,
                "approval_rules": rules,
            },
        )
    )
    return (
        error_response(
            request, status_code=status.HTTP_403_FORBIDDEN, code=ErrorCode.FORBIDDEN, message=message
        ),
        requirements,
    )


def _with_progress(
    approvals: list[Approval],
    context: TenantContext,
    repository: AnumRepository,
) -> list[Approval]:
    """Attach each approval's recorded approvers and the approvals it requires.

    A pending (or rejected or expired) approval shows what the current organization
    rules require; an approved one shows the approvals it actually collected.
    """
    if not approvals:
        return approvals
    recorded = repository.list_approval_approvers([approval.id for approval in approvals], context)
    governance = None
    result = []
    for approval in approvals:
        approvers = [row.view() for row in recorded.get(approval.id, [])]
        if approval.status == ApprovalStatus.APPROVED:
            required = max(1, len(approvers))
        else:
            if governance is None:
                governance = repository.get_tool_governance(context)
            match = match_governance(
                governance, tool=approval.action, target=approval.target, risk_level=approval.risk_level
            )
            required = required_approvals(decision_requirements(match)[0])
        result.append(approval.model_copy(update={"approvers": approvers, "required_approvals": required}))
    return result


async def _decide_approval_locked(
    approval_id: str,
    decision: ApprovalStatus,
    shown_hash: str | None,
    reason: str | None,
    request: Request,
    context: TenantContext,
    repository: AnumRepository,
) -> ApprovalDecisionResponse | JSONResponse:
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
    if approval.status == ApprovalStatus.EXPIRED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Approval expired")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")

    runtime = AgentRuntime(budgeted_model_gateway(context, model_gateway), repository, tools=tool_registry)
    run = repository.find_run_for_task(task.id, context)
    now = utc_now()
    if runtime.expire_if_due(task, approval, context, now):
        # Commit the expiry (and the failed run) but refuse the decision. Nothing is
        # raised, so the request's unit of work commits before the 410 is sent.
        if run_dispatcher is not None:
            if run:
                await run_dispatcher.approval_decided(context, task.id, approval.id)
        elif run is not None and run.checkpoint.approval_id == approval.id:
            runtime.begin_execution(task, run, context, approval=approval)
            repository.save_task(task)
            repository.save_run(run)
        return error_response(
            request,
            status_code=status.HTTP_410_GONE,
            code=ErrorCode.GONE,
            message="Approval expired; the action was not executed",
        )
    if shown_hash is not None or decision == ApprovalStatus.APPROVED:
        if approval.payload_hash is None or shown_hash != approval.payload_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approval payload does not match what was shown; reload and review it again",
            )
    requirements = _DecisionRequirements()
    if decision == ApprovalStatus.APPROVED:
        refusal, requirements = _governance_decision_refusal(
            request, context, repository, task, approval, now
        )
        if refusal is not None:
            return refusal
    if (
        decision == ApprovalStatus.APPROVED
        and approval.risk_level in TWO_PERSON_RISK_LEVELS
        and context.user_id in {task.created_by, approval.requested_by}
        and repository.get_approval_policy(context).two_person_rule
    ):
        # Nothing about the approval changes; the refusal itself is audited (and committed,
        # since nothing is raised) so repeated self-approval attempts are visible.
        repository.record_audit(
            AuditRecord(
                id=new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor=context.user_id,
                action="approval.self_approval_denied",
                target=approval.id,
                outcome="denied",
                correlation_id=task.id,
                created_at=now,
                metadata={"task_id": task.id, "tool": approval.action, "risk_level": approval.risk_level.value},
            )
        )
        return error_response(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.FORBIDDEN,
            message=(
                "This workspace requires two people for high-risk actions: you created or "
                "started this task, so another owner must approve it. You can still reject it."
            ),
        )

    approvers = repository.list_approval_approvers([approval.id], context).get(approval.id, [])
    if decision == ApprovalStatus.APPROVED:
        if any(approver.user_id == context.user_id for approver in approvers):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "You already approved this action; "
                    f"{max(1, requirements.required - len(approvers))} more approval(s) from "
                    "other people are needed"
                ),
            )
        approver = repository.add_approval_approver(
            ApprovalApproverRecord(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                approval_id=approval.id,
                user_id=context.user_id,
                payload_hash=approval.payload_hash or "",
                reason=reason,
                approved_at=now,
            ),
            context,
        )
        approvers = [*approvers, approver]
        if len(approvers) < requirements.required:
            return _record_partial_approval(
                context, repository, task, run, approval, approvers, requirements, reason, now
            )

    approval.status = decision
    approval.decided_at = now
    approval.decided_by = context.user_id
    approval.decision_reason = reason
    repository.save_approval(approval)
    decided_payload: dict[str, object] = {"task_id": task.id}
    if reason:
        decided_payload["reason"] = reason
    chain_metadata: dict[str, object] = {}
    if requirements.required > 1:
        chain_metadata = {
            "approvals": len(approvers),
            "required_approvals": requirements.required,
            "approvers": [approver.user_id for approver in approvers],
        }
        decided_payload.update(chain_metadata)
    repository.record_event(
        create_event(
            CanonicalEventName(f"approval.{decision.value}"),
            context,
            approval.id,
            decided_payload,
            correlation_id=task.id,
            created_at=approval.decided_at,
        ).event
    )
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=f"approval.{decision.value}",
            target=approval.id,
            outcome="success",
            correlation_id=task.id,
            created_at=now,
            metadata={
                "task_id": task.id,
                "tool": approval.action,
                "payload_hash": approval.payload_hash,
                "risk_level": approval.risk_level.value,
                "target": approval.target,
                "reason": reason,
                **chain_metadata,
            },
        )
    )
    (shown,) = _with_progress([approval], context, repository)
    if run_dispatcher is not None:
        # The workflow applies the decision; a lost signal is caught by its next poll.
        if run:
            await run_dispatcher.approval_decided(context, task.id, approval.id)
        return ApprovalDecisionResponse(approval=shown, task=task, run=run)
    resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
    repository.save_task(task)
    if resumed_run:
        repository.save_run(resumed_run)
    return ApprovalDecisionResponse(approval=shown, task=task, run=resumed_run)


def _record_partial_approval(
    context: TenantContext,
    repository: AnumRepository,
    task: Task,
    run: AgentRun | None,
    approval: Approval,
    approvers: list[ApprovalApproverRecord],
    requirements: _DecisionRequirements,
    reason: str | None,
    now: datetime,
) -> ApprovalDecisionResponse:
    """One approval of a chain that still needs more: audited, announced, still pending.

    The approval row is unchanged (status ``pending``), so neither the inline runtime
    nor the Temporal workflow resumes; the run keeps waiting for the last approval, a
    rejection or the expiry.
    """
    progress: dict[str, object] = {
        "task_id": task.id,
        "approvals": len(approvers),
        "required_approvals": requirements.required,
        "approvers": [approver.user_id for approver in approvers],
    }
    event_payload = dict(progress)
    if reason:
        event_payload["reason"] = reason
    repository.record_event(
        create_event(
            CanonicalEventName.APPROVAL_PARTIALLY_APPROVED,
            context,
            approval.id,
            event_payload,
            correlation_id=task.id,
            created_at=now,
        ).event
    )
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action="approval.partially_approved",
            target=approval.id,
            outcome="success",
            correlation_id=task.id,
            created_at=now,
            metadata={
                **progress,
                "tool": approval.action,
                "payload_hash": approval.payload_hash,
                "risk_level": approval.risk_level.value,
                "target": approval.target,
                "reason": reason,
                "approval_rules": requirements.rules,
            },
        )
    )
    (shown,) = _with_progress([approval], context, repository)
    return ApprovalDecisionResponse(approval=shown, task=task, run=run)
