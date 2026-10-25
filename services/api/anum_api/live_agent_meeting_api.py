"""Authenticated, PostgreSQL-only live meeting management API.

This is management of durable sessions, NOT autonomous agent execution.
Agent selection and model turns are deliberately not exposed until runtime
governance and database reservation adapters pass integration tests.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated, Literal
from .model_gateway import build_default_model_gateway
from .meeting_execution import execute_meeting_turn

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .errors import ApplicationError, ErrorCode
from . import live_agent_meeting_budget as meeting_budget
from .live_agent_meeting_audit import audit_event, audit_in_new_transaction
from .live_agent_meeting_store import LiveMeetingStoreError, SqlLiveMeetingStore
from .scoped_store import open_scoped_store
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent-meetings", tags=["agent meetings"])

# Owner decision of 25 October 2026 (finding S-03): transcripts are read by the workspace owner only.
# "Owner" is the workspace owner role, the one that holds ``workspace:manage``. Members and viewers
# still see that a meeting took place, its title, time, participants and status.
TRANSCRIPT_DENIED = ("Only a workspace owner can read meeting transcripts. "
                     "You can still see that the meeting took place, who attended and its status.")

MeetingId = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(StrictBody):
    max_turns: int = Field(default=24, ge=1, le=200)
    max_duration_seconds: int = Field(default=1800, ge=60, le=14400)
    max_tokens: int = Field(default=50000, ge=100, le=1000000)
    max_cost_usd: float = Field(default=2, ge=0, le=1000)
    max_participants: int = Field(default=8, ge=1, le=50)


class CreateMeeting(StrictBody):
    title: str = Field(min_length=1, max_length=200)
    limits: Limits = Field(default_factory=Limits)


class RevisionChange(StrictBody):
    expected_revision: int = Field(ge=1)


class AddAgent(RevisionChange):
    agent_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RunMeeting(StrictBody):
    run_key: str = Field(min_length=8,max_length=80,pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    turns: int = Field(default=2, ge=1, le=3)


class ExecuteTurn(StrictBody):
    request_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class StateChange(RevisionChange):
    status: Literal["active", "paused", "finished", "cancelled", "failed"]


def can_read_transcript(context: TenantContext) -> bool:
    return has_permission(context, Permission.WORKSPACE_MANAGE)


def require_transcript_reader(context: TenantContext, meeting_id: str, read_path: str) -> None:
    """403 with a clear message unless the caller is a workspace owner; the refusal is audited (no text)."""
    if can_read_transcript(context):
        return
    try:
        audit_in_new_transaction(context, "agent_meeting.transcript_denied", meeting_id, outcome="denied",
                                 meeting_id=meeting_id, read_path=read_path, reason="owner_only",
                                 role=",".join(context.roles))
    except Exception:  # noqa: BLE001 - the refusal stands even if the audit store is unreachable
        logger.warning("agent_meeting.transcript_denied audit failed", exc_info=True)
    raise HTTPException(status.HTTP_403_FORBIDDEN, TRANSCRIPT_DENIED)


def _error(exc: LiveMeetingStoreError) -> HTTPException | ApplicationError:
    if exc.code == "budget":
        return ApplicationError(ErrorCode.AGENT_MEETING_BUDGET_EXCEEDED, str(exc), status_code=status.HTTP_402_PAYMENT_REQUIRED)
    return HTTPException(
        status_code={"not_found":404, "forbidden":403, "conflict":409, "unavailable":503}.get(exc.code, 409),
        detail=str(exc),
    )


@contextmanager
def _store(context: TenantContext) -> Iterator[SqlLiveMeetingStore]:
    if settings.repository_backend != "postgresql":
        raise HTTPException(503, "Live meetings require a migrated PostgreSQL backend")
    with open_scoped_store(context, None, SqlLiveMeetingStore, user_scoped=True) as store:
        yield store


def _audit(store: SqlLiveMeetingStore, context: TenantContext, action: str, meeting_id: str) -> None:
    from .db.repository import SqlAlchemyRepository
    SqlAlchemyRepository(store.session).record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=meeting_id,
            outcome="success",
            correlation_id=new_id("correlation"),
            created_at=utc_now(),
            metadata={"resource": "agent_meeting", "meeting_id": meeting_id},
        )
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_meeting(body: CreateMeeting, context: TenantContext = Depends(tenant_context)) -> dict:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    data = {"id": new_id("agent_meeting"), "title": body.title.strip(), **body.limits.model_dump()}
    if not data["title"]:
        raise HTTPException(422, "Meeting title cannot be blank")
    with _store(context) as store:
        try:
            result = store.create(context, data)
            _audit(store, context, "agent_meeting.create", result["id"])
            return result
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


class BudgetUpdate(StrictBody):
    # USD per calendar month (UTC), whole cents. null returns to the default (USD 50).
    monthly_cost_limit_usd: Decimal | None = Field(ge=0, le=100000, max_digits=9, decimal_places=2)


@router.get("/budget")
def get_budget(context: TenantContext = Depends(tenant_context)) -> dict:
    """This month's agent meeting spend against the workspace cap. Every member may read it: it holds no transcript."""
    require_permission(context, Permission.TASK_READ)
    with _store(context) as store:
        state = meeting_budget.read_state(store, context)
    return {**state.view(), "can_manage": has_permission(context, Permission.WORKSPACE_MANAGE)}


@router.put("/budget")
def update_budget(body: BudgetUpdate, context: TenantContext = Depends(tenant_context)) -> dict:
    """Owner only. Sets (or with null resets) the monthly cap; audited with the old and new limit."""
    require_permission(context, Permission.WORKSPACE_MANAGE)
    with _store(context) as store:
        before, after = meeting_budget.set_limit(store, context, body.monthly_cost_limit_usd)
        audit_event(store.session, context, "agent_meeting.budget_update", f"agent_meeting_budget:{context.workspace_id}",
                    previous_limit_usd=before.limit_usd, new_limit_usd=after.limit_usd,
                    reason="reset_to_default" if body.monthly_cost_limit_usd is None else "owner_set",
                    used_usd=after.used_usd, period_start=after.period_start.isoformat())
        return {**after.view(), "can_manage": True}


@router.get("")
def list_meetings(
    cursor: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_permission(context, Permission.TASK_READ)
    with _store(context) as store:
        return store.list(context, cursor=cursor, limit=limit)


@router.get("/{meeting_id}")
def get_meeting(meeting_id: MeetingId, context: TenantContext = Depends(tenant_context)) -> dict:
    require_permission(context, Permission.TASK_READ)
    with _store(context) as store:
        result = store.get(context, meeting_id)
        if result is None:
            raise HTTPException(404, "Meeting not found")
        return {**dict(result), "participants": store.participants(context, meeting_id)}


@router.get("/{meeting_id}/turns")
def list_turns(
    meeting_id: MeetingId,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_permission(context, Permission.TASK_READ)
    require_transcript_reader(context, meeting_id, "turns")
    with _store(context) as store:
        if store.get(context, meeting_id) is None:
            raise HTTPException(404, "Meeting not found")
        turns = store.turns(context, meeting_id, after=after, limit=limit)
        return {"items": turns, "next_after": turns[-1]["sequence"] if len(turns) == limit else None}


@router.post("/{meeting_id}/join-self")
def join_self(
    meeting_id: MeetingId, body: RevisionChange,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    with _store(context) as store:
        try:
            result = store.add_self(context, meeting_id, new_id("participant"), body.expected_revision)
            _audit(store, context, "agent_meeting.join_self", meeting_id)
            return result
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


def _refuse_start_at_cap(context: TenantContext, meeting_id: str) -> None:
    """A meeting cannot start or resume once the workspace's monthly meeting budget is used up (S-02)."""
    with _store(context) as store:
        state = meeting_budget.read_state(store, context)
    if state.used_usd < state.limit_usd:
        return
    audit_in_new_transaction(context, "agent_meeting.turn", meeting_id, outcome="blocked", meeting_id=meeting_id,
                             reason="monthly_budget_start", limit_usd=state.limit_usd, used_usd=state.used_usd,
                             period_start=state.period_start.isoformat())
    raise _error(meeting_budget.MeetingBudgetReached(state))


@router.post("/{meeting_id}/state")
def change_state(
    meeting_id: MeetingId, body: StateChange,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    if body.status == "active":
        _refuse_start_at_cap(context, meeting_id)
    with _store(context) as store:
        try:
            result = store.transition(context, meeting_id, body.expected_revision, body.status)
            _audit(store, context, "agent_meeting.state_change", meeting_id)
            return result
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


@router.post("/{meeting_id}/agents", status_code=201)
def add_agent_participant(
    meeting_id: MeetingId, body: AddAgent,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    """Add a scoped agent identity before the meeting starts.

    The participant is a speaking persona, not an independent permission grant.
    This endpoint never grants the agent access to application tools.
    """
    require_permission(context, Permission.WORKSPACE_MANAGE)
    with _store(context) as store:
        meeting = store.get(context, meeting_id, lock=True)
        if meeting is None:
            raise HTTPException(404, "Meeting not found")
        if meeting["created_by"] != context.user_id:
            raise HTTPException(403, "Only the owner can add agents")
        if meeting["revision"] != body.expected_revision:
            raise HTTPException(409, "Meeting revision changed")
        if meeting["status"] != "scheduled":
            raise HTTPException(409, "Agents may only be added to scheduled meetings")
        agent = store.session.execute(text("""
            select id from agent_profiles
            where tenant_id=:tenant and workspace_id=:workspace and id=:agent
              and status='active'
        """), store.args(context, agent=body.agent_id)).mappings().first()
        if agent is None:
            raise HTTPException(404, "Active agent not found in this workspace")
        participants = store.participants(context, meeting_id)
        if any(p["actor_id"] == body.agent_id for p in participants):
            return dict(meeting)
        if len(participants) >= meeting["max_participants"]:
            raise HTTPException(409, "Participant cap reached")
        store.session.execute(text("""
            insert into agent_meeting_participants
            (id,tenant_id,workspace_id,session_id,kind,actor_id,added_by)
            values (:participant,:tenant,:workspace,:id,'agent',:agent,:owner)
        """), store.args(context, id=meeting_id, participant=new_id("participant"),
                          agent=body.agent_id, owner=context.user_id))
        updated = store._revise(context, meeting_id, body.expected_revision, {}, change_status=False)
        _audit(store,context,"agent_meeting.agent_add",meeting_id)
        return updated


@router.post("/{meeting_id}/execute-turn")
async def execute_turn(
    meeting_id: MeetingId, body: ExecuteTurn,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    """Generate exactly one metered, durable agent contribution.

    No arbitrary tools or side effects are exposed to the model.
    The request key is idempotent and must be reused after a client timeout.
    """
    require_permission(context, Permission.WORKSPACE_MANAGE)
    try:
        gateway = build_default_model_gateway()
        result = await execute_meeting_turn(context, meeting_id, body.request_key, gateway)
        return result
    except LiveMeetingStoreError as exc:
        raise _error(exc) from exc


@router.post("/{meeting_id}/run")
async def run_meeting(
    meeting_id: MeetingId, body: RunMeeting,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    """Run up to three sequential AI contributions in an idempotent round.

    One turn commits before the next. Retrying the same run_key reuses saved
    turns. This synchronous bounded round is not an unattended job worker.
    """
    require_permission(context, Permission.WORKSPACE_MANAGE)
    gateway = build_default_model_gateway()
    results = []
    try:
        for index in range(body.turns):
            key = body.run_key + ":" + str(index + 1)
            result = await execute_meeting_turn(context, meeting_id, key, gateway)
            results.append(result)
        return {"items": results, "completed": len(results), "run_key": body.run_key}
    except LiveMeetingStoreError as exc:
        if results:
            return {"items":results,"completed":len(results),"run_key":body.run_key,
                    "stopped_reason":str(exc)}
        raise _error(exc) from exc
