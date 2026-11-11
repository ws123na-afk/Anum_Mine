"""Routes of the action browser for the web app (docs/action-browser.md#api).

* Saved logins and cards: owners only (``organization:manage``); lists never decrypt.
* Sessions and their step log: anyone who can read tasks; the runs list (``GET /runs``) only
  shows sessions whose task the caller can read (a teammate's private task stays hidden).
* The live picture, the owner's input for a "Needs you" step and Stop: owners (Stop also the
  person who created the task).

Async routes do their database work in worker threads (one short unit each) and await only the
browser service's socket (docs/realtime.md#event-loop).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .. import dependencies as deps
from ..audit import AuditRecord
from ..authorization import Permission
from ..schemas import TaskStatus, TenantContext, new_id, utc_now
from .store import SessionRecord, StepRecord, TERMINAL, open_action_store
from .vault import CredentialProblem, CredentialView, CredentialWrite, create_credential, delete_credential, list_credentials, update_credential

router = APIRouter(prefix="/api/v1/action-browser", tags=["action-browser"])

OWNER_ONLY = "Only a workspace owner manages saved logins and cards."
WATCH_DENIED = "Only a workspace owner sees the live browser and gives it input."
STOP_DENIED = "Only a workspace owner or the person who created this task can stop it."
NOT_FOUND = "That browser session is not in this workspace."


class StepView(BaseModel):
    seq: int
    kind: str
    summary: str
    url: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SessionView(BaseModel):
    id: str
    task_id: str
    run_id: str
    status: str
    goal: str
    start_url: str
    sites: list[str]
    logins: list[str]
    cards: list[str]
    step_count: int
    current_url: str | None = None
    current_title: str | None = None
    waiting: str | None = None
    pending: dict[str, Any] | None = None
    outcome: str | None = None
    message: str = ""
    live: bool
    can_watch: bool
    can_stop: bool
    created_at: str
    updated_at: str
    ended_at: str | None = None
    steps: list[StepView] = Field(default_factory=list)


class InputEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["click", "text", "key", "scroll"]
    x: float | None = Field(default=None, ge=0, le=1280)
    y: float | None = Field(default=None, ge=0, le=800)
    text: str | None = Field(default=None, max_length=200)
    key: str | None = Field(default=None, max_length=20)
    dy: float | None = Field(default=None, ge=-2000, le=2000)


def _step(row: StepRecord) -> StepView:
    return StepView(seq=row.seq, kind=row.kind, summary=row.summary, url=row.url, detail=row.detail, created_at=row.created_at.isoformat())


def _view(context: TenantContext, record: SessionRecord, steps: list[StepRecord], task_creator: str | None) -> SessionView:
    pending = dict(record.pending) if record.pending else None
    if pending is not None:
        pending.pop("url_secret", None)
    return SessionView(
        id=record.id, task_id=record.task_id, run_id=record.run_id, status=record.status, goal=record.goal, start_url=record.start_url,
        sites=record.sites, logins=sorted(record.logins), cards=record.cards, step_count=record.step_count, current_url=record.current_url,
        current_title=record.current_title, waiting=record.waiting, pending=pending, outcome=record.outcome, message=record.message,
        live=bool(record.browser_id) and not record.ended,
        can_watch=deps.has_permission(context, Permission.ORGANIZATION_MANAGE),
        can_stop=not record.ended and (deps.has_permission(context, Permission.ORGANIZATION_MANAGE) or (task_creator is not None and task_creator == context.user_id)),
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
        ended_at=record.ended_at.isoformat() if record.ended_at else None, steps=[_step(row) for row in steps],
    )


def _client() -> Any:
    from ..settings import settings
    from .tool import ActionServiceClient, configured_act_handler

    handler = configured_act_handler(settings, None, lambda context: None)  # type: ignore[arg-type,return-value]
    return handler.client if isinstance(handler.client, ActionServiceClient) else None


# The browser service client; tests replace it with one on a fake service.
service_client: Any = None


def client() -> Any:
    global service_client
    if service_client is None:
        service_client = _client()
    return service_client


# --------------------------------------------------------------------------- credentials


@router.get("/credentials", response_model=list[CredentialView])
def get_credentials(context: TenantContext = Depends(deps.tenant_context)) -> list[CredentialView]:
    _owner(context, OWNER_ONLY)
    return list_credentials(context)


@router.post("/credentials", response_model=CredentialView, status_code=status.HTTP_201_CREATED)
def post_credential(payload: CredentialWrite, context: TenantContext = Depends(deps.tenant_context)) -> CredentialView:
    _owner(context, OWNER_ONLY)
    try:
        return create_credential(context, payload)
    except CredentialProblem as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.put("/credentials/{credential_id}", response_model=CredentialView)
def put_credential(credential_id: str, payload: CredentialWrite, context: TenantContext = Depends(deps.tenant_context)) -> CredentialView:
    _owner(context, OWNER_ONLY)
    try:
        saved = update_credential(context, credential_id, payload)
    except CredentialProblem as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That saved login or card is not in this workspace.")
    return saved


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_credential(credential_id: str, context: TenantContext = Depends(deps.tenant_context)) -> Response:
    _owner(context, OWNER_ONLY)
    if not delete_credential(context, credential_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That saved login or card is not in this workspace.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _owner(context: TenantContext, detail: str) -> None:
    if not deps.has_permission(context, Permission.ORGANIZATION_MANAGE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# --------------------------------------------------------------------------- sessions


def _task_state(units: deps.UnitsOfWork, context: TenantContext, task_id: str) -> tuple[str | None, str | None]:
    """The task's status and creator, read in its own short unit (None when it is gone)."""
    with units(context) as repository:
        task = repository.get_task(task_id, context)
        return (task.status.value if task else None, task.created_by if task else None)


def _sessions(context: TenantContext, task_id: str, after: int) -> list[tuple[SessionRecord, list[StepRecord]]]:
    with open_action_store(context) as store:
        return [(record, store.steps(context, record.id, after=after, limit=300)) for record in store.sessions_for_task(context, task_id)]


def _one(context: TenantContext, session_id: str, after: int) -> tuple[SessionRecord, list[StepRecord]] | None:
    with open_action_store(context) as store:
        record = store.get_session(context, session_id)
        return (record, store.steps(context, record.id, after=after, limit=300)) if record else None


def _settle_abandoned(context: TenantContext, record: SessionRecord, task_status: str | None, *, stopped_by: str | None = None) -> SessionRecord | None:
    """End a session whose task ended without it (a rejected approval, a cancel, a lapsed approval)."""
    with open_action_store(context) as store:
        current = store.get_session(context, record.id)
        if current is None or current.status in TERMINAL:
            return current
        now = utc_now()
        current.status = "stopped" if stopped_by or task_status == TaskStatus.CANCELLED.value else "failed"
        current.outcome = "stopped" if current.status == "stopped" else "task_ended"
        current.message = ("Stopped by the owner." if stopped_by else
                           "The task ended (an approval was rejected, lapsed or the task was cancelled); the browser was closed.")
        current.waiting = None
        current.pending = None
        current.browser_id = None
        current.ended_at = current.updated_at = now
        store.save_session(context, current)
        store.add_step(context, StepRecord(session_id=current.id, seq=0, kind="stopped" if current.status == "stopped" else "failed",
                                           summary=current.message, url=current.current_url, created_at=now))
        if stopped_by:
            store.record_audit(AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=stopped_by,
                action="action_browser.session_stopped", target=f"action_browser_session:{current.id}", outcome="success",
                correlation_id=current.task_id or new_id("correlation"), created_at=now, metadata={"task_id": current.task_id, "steps": current.step_count},
            ))
        return current


async def _reconcile(context: TenantContext, units: deps.UnitsOfWork, record: SessionRecord) -> SessionRecord:
    if record.ended or not record.task_id:
        return record
    task_status, _ = await asyncio.to_thread(_task_state, units, context, record.task_id)
    if task_status in {None, TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
        browser_id = record.browser_id
        settled = await asyncio.to_thread(_settle_abandoned, context, record, task_status)
        if browser_id and client() is not None:
            await client().close(browser_id)
        return settled or record
    return record


@router.get("/sessions", response_model=list[SessionView])
async def list_sessions(
    task_id: str = Query(min_length=1, max_length=80),
    after: int = Query(default=0, ge=0),
    context: TenantContext = Depends(deps.tenant_context),
    units: deps.UnitsOfWork = Depends(deps.units_of_work),
) -> list[SessionView]:
    deps.require_permission(context, Permission.TASK_READ)
    _, creator = await asyncio.to_thread(_task_state, units, context, task_id)
    found = await asyncio.to_thread(_sessions, context, task_id, after)
    views = []
    for record, steps in found:
        record = await _reconcile(context, units, record)
        if record.ended and record.status in TERMINAL and steps and steps[-1].kind not in {"done", "failed", "stopped"}:
            steps = (await asyncio.to_thread(_one, context, record.id, after) or (record, steps))[1]
        views.append(_view(context, record, steps, creator))
    return views


class RunPage(BaseModel):
    """One page of the workspace's browser runs, newest first, without their step logs."""

    items: list[SessionView]
    limit: int
    offset: int
    next_offset: int | None = None


def _recent(units: deps.UnitsOfWork, context: TenantContext, limit: int, offset: int) -> tuple[list[tuple[SessionRecord, str | None]], bool]:
    """A page of sessions whose task the caller may read, each with its task's creator, and whether more follow.

    The store pages over every session of the workspace; a session whose task is not visible (another
    person's private task, or a task that was deleted) is left out of the page rather than counted, so a
    page may hold fewer than ``limit`` items while ``next_offset`` still says where the next one starts.
    """
    with open_action_store(context) as store:
        rows = store.recent_sessions(context, limit=limit + 1, offset=offset)
    more, rows = len(rows) > limit, rows[:limit]
    owner = deps.has_permission(context, Permission.ORGANIZATION_MANAGE)
    visible: list[tuple[SessionRecord, str | None]] = []
    with units(context) as repository:
        for record in rows:
            if not record.task_id:
                if owner:
                    visible.append((record, None))
                continue
            task = repository.get_task(record.task_id, context)
            if task is not None:
                visible.append((record, task.created_by))
    return visible, more


@router.get("/runs", response_model=RunPage)
async def list_runs(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    context: TenantContext = Depends(deps.tenant_context),
    units: deps.UnitsOfWork = Depends(deps.units_of_work),
) -> RunPage:
    """Every browser run of the workspace the caller may see, newest first (the phone's runs list)."""
    deps.require_permission(context, Permission.TASK_READ)
    found, more = await asyncio.to_thread(_recent, units, context, limit, offset)
    items = []
    for record, creator in found:
        record = await _reconcile(context, units, record)
        items.append(_view(context, record, [], creator))
    return RunPage(items=items, limit=limit, offset=offset, next_offset=offset + limit if more else None)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(
    session_id: str,
    after: int = Query(default=0, ge=0),
    context: TenantContext = Depends(deps.tenant_context),
    units: deps.UnitsOfWork = Depends(deps.units_of_work),
) -> SessionView:
    deps.require_permission(context, Permission.TASK_READ)
    found = await asyncio.to_thread(_one, context, session_id, after)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    record, steps = found
    reconciled = await _reconcile(context, units, record)
    if reconciled.status != record.status:
        record, steps = await asyncio.to_thread(_one, context, session_id, after) or (reconciled, steps)
    _, creator = await asyncio.to_thread(_task_state, units, context, record.task_id) if record.task_id else (None, None)
    return _view(context, record, steps, creator)


@router.get("/sessions/{session_id}/frame", response_class=Response, responses={200: {"content": {"image/jpeg": {}}}})
async def get_frame(session_id: str, context: TenantContext = Depends(deps.tenant_context)) -> Response:
    """The live picture of the page (masked, never stored). 409 when the session has no browser."""
    _owner(context, WATCH_DENIED)
    found = await asyncio.to_thread(_one, context, session_id, 10**9)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    record, _ = found
    if record.ended or not record.browser_id or client() is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The browser session has ended; there is no live picture.")
    try:
        picture = await client().screenshot(record.browser_id)
    except Exception:
        picture = None
    if picture is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The browser could not take a picture of the page right now.")
    return Response(picture, media_type="image/jpeg", headers={"cache-control": "no-store", "x-content-type-options": "nosniff"})


def _audit_input(context: TenantContext, record: SessionRecord, kind: str) -> None:
    with open_action_store(context) as store:
        store.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="action_browser.input", target=f"action_browser_session:{record.id}", outcome="success",
            correlation_id=record.task_id or new_id("correlation"), created_at=utc_now(), metadata={"kind": kind},
        ))


@router.post("/sessions/{session_id}/input")
async def post_input(session_id: str, payload: InputEvent, context: TenantContext = Depends(deps.tenant_context)) -> dict[str, str]:
    """The owner's own click, text, key or scroll, only while the session needs a person."""
    _owner(context, WATCH_DENIED)
    found = await asyncio.to_thread(_one, context, session_id, 10**9)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    record, _ = found
    if record.ended or record.waiting != "handoff" or not record.browser_id or client() is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The browser takes your input only while it waits for you (Needs you).")
    from .tool import ServiceAnswer

    try:
        await client().owner_input(record.browser_id, payload.model_dump(exclude_none=True))
    except ServiceAnswer as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)[:300]) from None
    await asyncio.to_thread(_audit_input, context, record, payload.kind)
    return {"outcome": "done"}


@router.post("/sessions/{session_id}/stop", response_model=SessionView)
async def stop_session(
    session_id: str,
    context: TenantContext = Depends(deps.tenant_context),
    units: deps.UnitsOfWork = Depends(deps.units_of_work),
) -> SessionView:
    """Close the browser, end the session and cancel its task (and so expire its pending approval)."""
    deps.require_permission(context, Permission.TASK_READ)
    found = await asyncio.to_thread(_one, context, session_id, 0)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    record, _ = found
    task_status, creator = await asyncio.to_thread(_task_state, units, context, record.task_id) if record.task_id else (None, None)
    if not (deps.has_permission(context, Permission.ORGANIZATION_MANAGE) or (creator is not None and creator == context.user_id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=STOP_DENIED)
    browser_id = record.browser_id
    settled = await asyncio.to_thread(_settle_abandoned, context, record, task_status, stopped_by=context.user_id)
    if browser_id and client() is not None:
        await client().close(browser_id)
    if task_status in {TaskStatus.RUNNING.value, TaskStatus.WAITING_APPROVAL.value, TaskStatus.QUEUED.value, TaskStatus.CREATED.value}:
        from ..main import cancel_task_record, run_dispatcher

        try:
            task, run = await deps.run_in_unit(units, context, lambda repository: cancel_task_record(record.task_id, context, repository))
        except HTTPException:
            task, run = None, None
        if task is not None and run_dispatcher is not None and run is not None:
            await run_dispatcher.cancelled(context, task.id)
    found = await asyncio.to_thread(_one, context, session_id, 0)
    final = found[0] if found else (settled or record)
    return _view(context, final, found[1] if found else [], creator)


__all__ = ["router"]
