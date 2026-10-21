"""Authenticated, PostgreSQL-only live meeting management API.

This is management of durable sessions, NOT autonomous agent execution.
Agent selection and model turns are deliberately not exposed until runtime
governance and database reservation adapters pass integration tests.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .live_agent_meeting_store import LiveMeetingStoreError, SqlLiveMeetingStore
from .scoped_store import open_scoped_store
from .schemas import TenantContext, new_id
from .settings import settings

router = APIRouter(prefix="/api/v1/agent-meetings", tags=["agent meetings"])

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


class StateChange(RevisionChange):
    status: Literal["active", "paused", "finished", "cancelled", "failed"]


def _error(exc: LiveMeetingStoreError) -> HTTPException:
    return HTTPException(
        status_code={"not_found":404, "forbidden":403, "conflict":409}.get(exc.code, 409),
        detail=str(exc),
    )


@contextmanager
def _store(context: TenantContext) -> Iterator[SqlLiveMeetingStore]:
    if settings.repository_backend != "postgresql":
        raise HTTPException(503, "Live meetings require a migrated PostgreSQL backend")
    with open_scoped_store(context, None, SqlLiveMeetingStore, user_scoped=True) as store:
        yield store


@router.post("", status_code=status.HTTP_201_CREATED)
def create_meeting(body: CreateMeeting, context: TenantContext = Depends(tenant_context)) -> dict:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    data = {"id": new_id("agent_meeting"), "title": body.title.strip(), **body.limits.model_dump()}
    if not data["title"]:
        raise HTTPException(422, "Meeting title cannot be blank")
    with _store(context) as store:
        try:
            return store.create(context, data)
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


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
            return store.add_self(context, meeting_id, new_id("participant"), body.expected_revision)
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


@router.post("/{meeting_id}/state")
def change_state(
    meeting_id: MeetingId, body: StateChange,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    with _store(context) as store:
        try:
            return store.transition(context, meeting_id, body.expected_revision, body.status)
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc
