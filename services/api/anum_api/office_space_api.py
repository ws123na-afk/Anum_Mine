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

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
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
CreateKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")]


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


class RoomGeometryUpdate(StrictBody):
    expected_revision: int = Field(ge=1)
    x: float = Field(ge=-10000,le=10000)
    z: float = Field(ge=-10000,le=10000)
    width: float = Field(ge=1,le=1000)
    depth: float = Field(ge=1,le=1000)


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


def _create(
    context: TenantContext, kind: ItemKind, body: StrictBody,
    key: str | None, response: Response,
) -> dict:
    with _store(context) as store:
        try:
            item_id = body.id
            fields = body.model_dump(exclude={"id"})
            if kind == "desks":
                fields["agent_id"] = None
            row, created = store.create_with_key(context, kind, item_id, fields, key)
            if created:
                _audit(store, context, f"office.{kind[:-1]}.create", item_id)
            else:
                response.status_code = status.HTTP_200_OK
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


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
def create_building(body: BuildingCreate, response: Response, idempotency_key: CreateKey = None, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(422, "Invalid IANA time zone") from exc
    return _create(context, "buildings", body, idempotency_key, response)


@router.post("/floors", status_code=status.HTTP_201_CREATED)
def create_floor(body: FloorCreate, response: Response, idempotency_key: CreateKey = None, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    return _create(context, "floors", body, idempotency_key, response)


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
def create_room(body: RoomCreate, response: Response, idempotency_key: CreateKey = None, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    return _create(context, "rooms", body, idempotency_key, response)


@router.post("/desks", status_code=status.HTTP_201_CREATED)
def create_desk(body: DeskCreate, response: Response, idempotency_key: CreateKey = None, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    return _create(context, "desks", body, idempotency_key, response)


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


@router.patch("/rooms/{room_id}/geometry")
def update_room_geometry(room_id: Identifier, body: RoomGeometryUpdate,
                         context: TenantContext = Depends(tenant_context)) -> dict:
    """Audited, optimistic room placement with floor-level collision locking."""
    _writable(context)
    with _store(context) as store:
        try:
            result=store.update_room_geometry(context,room_id,body.expected_revision,
                                               body.x,body.z,body.width,body.depth)
            _audit(store,context,"office.room.geometry",room_id)
            return result
        except SpaceStoreError as exc:
            raise _error(exc) from exc
