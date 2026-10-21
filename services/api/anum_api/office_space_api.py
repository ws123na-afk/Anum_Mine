"""Permissioned ANUM MINE office layout API.

Reads require task:read. Mutations require workspace:manage (owner-level).
Writes use RLS-scoped PostgreSQL transactions, a visible-agent check, revision
preconditions and append-only audit records. Never trust scope supplied by a client.
The office feature remains unavailable in non-PostgreSQL installations.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Annotated, Literal
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .office_space_store import SpaceStoreError, SqlOfficeSpaceStore
from .scoped_store import open_scoped_store
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

router = APIRouter(prefix="/api/v1/office-spaces", tags=["office spaces"])

ItemKind = Literal["buildings", "floors", "rooms", "desks"]
ResourceKind = Annotated[ItemKind, Path()]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildingCreate(StrictBody):
    id: Identifier
    name: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=3, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class FloorCreate(StrictBody):
    id: Identifier
    building_id: Identifier
    level: int = Field(ge=-20, le=120)
    name: str = Field(min_length=1, max_length=100)


class RoomCreate(StrictBody):
    id: Identifier
    floor_id: Identifier
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["office", "meeting", "reception", "workshop", "warehouse", "lounge"]
    capacity: int = Field(ge=1, le=5000)


class DeskCreate(StrictBody):
    id: Identifier
    room_id: Identifier
    label: str = Field(min_length=1, max_length=100)


class RenameRoom(StrictBody):
    name: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class AssignDesk(StrictBody):
    agent_id: Identifier | None
    expected_revision: int = Field(ge=1)


def _readable(context: TenantContext) -> None:
    require_permission(context, Permission.TASK_READ)


def _writable(context: TenantContext) -> None:
    require_permission(context, Permission.WORKSPACE_MANAGE)


@contextmanager
def _store(context: TenantContext) -> Iterator[SqlOfficeSpaceStore]:
    if settings.repository_backend != "postgresql":
        raise HTTPException(status_code=503, detail="Office persistence needs the PostgreSQL backend")
    # Setting anum.user_id is required by agent_profiles' private-agent RLS.
    with open_scoped_store(context, None, SqlOfficeSpaceStore, user_scoped=True) as store:
        yield store


def _error(exc: SpaceStoreError) -> HTTPException:
    return HTTPException(
        status_code={"invalid": 422, "not_found": 404, "conflict": 409, "restricted": 409}.get(exc.code, 409),
        detail=str(exc),
    )


def _audit(store: SqlOfficeSpaceStore, context: TenantContext, action: str, target: str) -> None:
    from .db.repository import SqlAlchemyRepository
    SqlAlchemyRepository(store.session).record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome="success",
            correlation_id=new_id("correlation"),
            created_at=utc_now(),
            metadata={"office_resource": target},
        )
    )


@router.get("/{kind}")
def list_spaces(
    kind: ResourceKind,
    parent_id: str | None = Query(default=None, max_length=80),
    cursor: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
) -> dict:
    _readable(context)
    with _store(context) as store:
        return store.list(context, kind, parent_id, cursor, limit)


@router.get("/{kind}/{item_id}")
def get_space(kind: ResourceKind, item_id: Identifier, context: TenantContext = Depends(tenant_context)) -> dict:
    _readable(context)
    with _store(context) as store:
        row = store.get(context, kind, item_id)
        if row is None:
            raise HTTPException(404, "Office record not found")
        return row


@router.post("/buildings", status_code=status.HTTP_201_CREATED)
def create_building(body: BuildingCreate, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(422, "Invalid IANA time zone") from exc
    with _store(context) as store:
        try:
            row = store.create(context, "buildings", body.id, body.model_dump(exclude={"id"}))
            _audit(store, context, "office.building.create", body.id)
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/floors", status_code=status.HTTP_201_CREATED)
def create_floor(body: FloorCreate, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            row = store.create(context, "floors", body.id, body.model_dump(exclude={"id"}))
            _audit(store, context, "office.floor.create", body.id)
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
def create_room(body: RoomCreate, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            row = store.create(context, "rooms", body.id, body.model_dump(exclude={"id"}))
            _audit(store, context, "office.room.create", body.id)
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/desks", status_code=status.HTTP_201_CREATED)
def create_desk(body: DeskCreate, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            row = store.create(context, "desks", body.id, {**body.model_dump(exclude={"id"}), "agent_id": None})
            _audit(store, context, "office.desk.create", body.id)
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.patch("/rooms/{room_id}")
def rename_room(room_id: Identifier, body: RenameRoom, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            row = store.rename_room(context, room_id, body.name, body.expected_revision)
            _audit(store, context, "office.room.rename", room_id)
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.patch("/desks/{desk_id}/assignment")
def assign_desk(desk_id: Identifier, body: AssignDesk, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            row = store.assignment(context, desk_id, body.agent_id, body.expected_revision)
            _audit(store, context, "office.desk.assignment", desk_id)
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(
    room_id: Identifier,
    expected_revision: int = Query(ge=1),
    context: TenantContext = Depends(tenant_context),
) -> None:
    _writable(context)
    with _store(context) as store:
        try:
            store.delete_room(context, room_id, expected_revision)
            _audit(store, context, "office.room.delete", room_id)
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.delete("/desks/{desk_id}", status_code=204)
def delete_desk(
    desk_id: Identifier,
    expected_revision: int = Query(ge=1),
    context: TenantContext = Depends(tenant_context),
) -> None:
    _writable(context)
    with _store(context) as store:
        try:
            store.delete_desk(context, desk_id, expected_revision)
            _audit(store, context, "office.desk.delete", desk_id)
        except SpaceStoreError as exc:
            raise _error(exc) from exc
