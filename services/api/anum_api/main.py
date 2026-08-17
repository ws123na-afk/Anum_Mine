import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .authorization import Permission
from .dependencies import (
    idempotency_key_header,
    memory_repository,
    memory_repository_context,
    repository_context,
    require_permission,
    tenant_context,
)
from .errors import register_exception_handlers
from .events import CanonicalEventName, create_event
from .events_nats import NatsEventPublisher, build_nats_publisher
from .idempotency_support import run_idempotently
from .model_gateway import MockModelGateway
from .memory import (
    MemoryAccess,
    MemoryCreate,
    MemoryListFilters,
    MemoryNote,
    MemoryRepository,
    MemoryService,
)
from .realtime import router as realtime_router
from .repository import AnumRepository
from .routes_files import router as files_router
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
from .rate_limit import RateLimitMiddleware
from .security_headers import SecurityHeadersMiddleware
from .settings import settings
from .store import store
from .valkey_client import build_redis_client
from .request_context import CORRELATION_ID_HEADER, CorrelationIdMiddleware
from .workflows.client import (
    signal_approval_decision as _signal_temporal_approval_decision,
    signal_cancel as _signal_temporal_cancel,
    start_task_workflow as _start_temporal_task_workflow,
    wait_for_task_change as _wait_for_temporal_task_change,
)
from .workflows.worker import run_worker_in_background

nats_publisher: NatsEventPublisher | None = build_nats_publisher()


async def _publish_to_nats(event: DomainEvent) -> None:
    """Best-effort NATS publish alongside the durable repository write.

    A NATS outage must never fail the HTTP request that's already durably
    recorded the event via `repository.record_event` - realtime.py's SSE
    endpoint falls back to polling the repository directly, so a dropped
    publish only costs a little latency for connected clients, not data.
    """

    if nats_publisher is None:
        return
    try:
        await nats_publisher.publish(event)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to publish event %s to NATS; it remains durable in the repository", event.id
        )


@asynccontextmanager
async def _lifespan(_: FastAPI):
    worker_task = await run_worker_in_background()
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        if nats_publisher is not None:
            await nats_publisher.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=_lifespan)
app.add_middleware(SecurityHeadersMiddleware, hsts=settings.security_headers_hsts_enabled)
if settings.rate_limit_enabled:
    app.add_middleware(
        RateLimitMiddleware,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
        trust_forwarded_for=settings.rate_limit_trust_forwarded_for,
        redis_client=build_redis_client(settings.valkey_url),
    )
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
    allow_headers=[
        "content-type",
        "authorization",
        "idempotency-key",
        "x-tenant-id",
        "x-workspace-id",
        "x-user-id",
        "x-user-roles",
        CORRELATION_ID_HEADER,
    ],
    expose_headers=[CORRELATION_ID_HEADER],
)
register_exception_handlers(app)
app.include_router(realtime_router)
app.include_router(files_router)
repository = memory_repository


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.post("/api/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.TASK_CREATE)

    async def _create_task() -> tuple[int, Task]:
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
        created_event = create_event(
            CanonicalEventName.TASK_CREATED,
            context,
            task.id,
            {"title": task.title},
            correlation_id=task.id,
            created_at=now,
        ).event
        repository.record_event(created_event)
        await _publish_to_nats(created_event)
        return status.HTTP_201_CREATED, task

    return await run_idempotently(
        context=context,
        action="task.create",
        key=idempotency_key,
        payload=payload,
        execute=_create_task,
    )


@app.get("/api/v1/tasks", response_model=list[Task])
async def list_tasks(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Task]:
    require_permission(context, Permission.TASK_READ)
    return repository.list_tasks(context)


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
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.TASK_RUN)

    async def _run_task() -> tuple[int, RunTaskResponse]:
        task = _get_task_for_context(task_id, context, repository, for_update=True)
        if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task cannot be run from current state",
            )

        if settings.temporal_address:
            # Durable path: a Temporal workflow does the mutation (see
            # workflows/task_workflow.py); wait briefly for its first
            # activity to land, then read the resulting state back from the
            # repository so the response shape matches the non-Temporal path
            # exactly - see workflows/client.py's wait_for_task_change.
            starting_status = task.status
            await _start_temporal_task_workflow(task.id, context)
            try:
                task = await _wait_for_temporal_task_change(
                    repository, task.id, context, from_status=starting_status
                )
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=status.HTTP_202_ACCEPTED,
                    detail="Task run started but has not completed yet",
                ) from exc
            # The activity saves the task before the run (see
            # workflows/activities.py's run_agent_activity) - close a narrow
            # window where the task's new status is already visible but the
            # run row isn't yet, rather than returning a response with a
            # required-but-missing `run`.
            run = repository.find_run_for_task(task.id, context)
            for _ in range(20):
                if run is not None:
                    break
                await asyncio.sleep(0.05)
                run = repository.find_run_for_task(task.id, context)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_202_ACCEPTED,
                    detail="Task run started but has not completed yet",
                )
            approval = next(
                (
                    approval
                    for approval in repository.list_approvals(context)
                    if approval.task_id == task.id and approval.status.value == "pending"
                ),
                None,
            )
            return status.HTTP_200_OK, RunTaskResponse(task=task, run=run, approval=approval)

        runtime = AgentRuntime(MockModelGateway(), repository)
        run, approval = await runtime.run_task(task, context)
        repository.save_task(task)
        repository.save_run(run)
        return status.HTTP_200_OK, RunTaskResponse(task=task, run=run, approval=approval)

    return await run_idempotently(
        context=context,
        action="task.run",
        key=idempotency_key,
        payload={"task_id": task_id},
        execute=_run_task,
    )


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=Task)
async def cancel_task(
    task_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.TASK_CANCEL)

    async def _cancel_task() -> tuple[int, Task]:
        task = _get_task_for_context(task_id, context, repository, for_update=True)
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be cancelled")

        if settings.temporal_address and task.status == TaskStatus.WAITING_APPROVAL:
            # A task only reaches WAITING_APPROVAL via the Temporal path when
            # Temporal is configured (see run_task above), so its workflow is
            # durably parked in workflow.wait_condition - signal it rather
            # than mutating state directly, so the workflow's own
            # cancel_task_activity (identical logic, run once, durably) does
            # the work instead of racing a second, independent mutation here.
            try:
                await _signal_temporal_cancel(task.id)
                cancelled = await _wait_for_temporal_task_change(
                    repository, task.id, context, from_status=TaskStatus.WAITING_APPROVAL
                )
                return status.HTTP_200_OK, cancelled
            except Exception:
                logging.getLogger(__name__).warning(
                    "Temporal cancel signal failed for task %s; falling back to direct cancel",
                    task.id,
                    exc_info=True,
                )

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
        cancelled_event = create_event(
            CanonicalEventName.TASK_CANCELLED,
            context,
            task.id,
            {"task_id": task.id},
            correlation_id=task.id,
        ).event
        repository.record_event(cancelled_event)
        await _publish_to_nats(cancelled_event)
        return status.HTTP_200_OK, task

    return await run_idempotently(
        context=context,
        action="task.cancel",
        key=idempotency_key,
        payload={"task_id": task_id},
        execute=_cancel_task,
    )


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
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.MEMORY_CREATE)

    async def _create_memory() -> tuple[int, MemoryNote]:
        if repository.get_task(payload.task_id, context) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        try:
            note = MemoryService(memories).create(context, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return status.HTTP_201_CREATED, note

    return await run_idempotently(
        context=context,
        action="memory.create",
        key=idempotency_key,
        payload=payload,
        execute=_create_memory,
    )


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
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.MEMORY_DELETE)

    async def _delete_memory() -> tuple[int, None]:
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
        return status.HTTP_204_NO_CONTENT, None

    return await run_idempotently(
        context=context,
        action="memory.delete",
        key=idempotency_key,
        payload={"memory_id": memory_id},
        execute=_delete_memory,
    )


@app.post("/api/v1/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.APPROVAL_DECIDE)
    return await run_idempotently(
        context=context,
        action="approval.approve",
        key=idempotency_key,
        payload={"approval_id": approval_id},
        execute=lambda: _decide_approval(
            approval_id, ApprovalStatus.APPROVED, context, repository
        ),
    )


@app.post("/api/v1/approvals/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject(
    approval_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.APPROVAL_DECIDE)
    return await run_idempotently(
        context=context,
        action="approval.reject",
        key=idempotency_key,
        payload={"approval_id": approval_id},
        execute=lambda: _decide_approval(
            approval_id, ApprovalStatus.REJECTED, context, repository
        ),
    )


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
) -> tuple[int, ApprovalDecisionResponse]:
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

    if settings.temporal_address and task.status == TaskStatus.WAITING_APPROVAL:
        # See cancel_task's matching comment: a WAITING_APPROVAL task, with
        # Temporal configured, has its workflow durably parked on this exact
        # decision - signal it instead of mutating state here directly.
        try:
            await _signal_temporal_approval_decision(task.id, decision.value)
            resolved_task = await _wait_for_temporal_task_change(
                repository, task.id, context, from_status=TaskStatus.WAITING_APPROVAL
            )
            resolved_approval = repository.get_approval(approval_id, context)
            resolved_run = repository.find_run_for_task(task.id, context)
            return status.HTTP_200_OK, ApprovalDecisionResponse(
                approval=resolved_approval, task=resolved_task, run=resolved_run
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "Temporal approval signal failed for task %s; falling back to direct decision",
                task.id,
                exc_info=True,
            )

    approval.status = decision
    approval.decided_at = utc_now()
    repository.save_approval(approval)
    run = repository.find_run_for_task(task.id, context)
    decision_event = create_event(
        CanonicalEventName(f"approval.{decision.value}"),
        context,
        approval.id,
        {"task_id": task.id},
        correlation_id=task.id,
        created_at=approval.decided_at,
    ).event
    repository.record_event(decision_event)
    await _publish_to_nats(decision_event)
    runtime = AgentRuntime(MockModelGateway(), repository)
    resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
    repository.save_task(task)
    if resumed_run:
        repository.save_run(resumed_run)
    return status.HTTP_200_OK, ApprovalDecisionResponse(approval=approval, task=task, run=resumed_run)
