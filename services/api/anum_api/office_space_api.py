"""Permissioned ANUM MINE office layout API.

Reads require task:read. Mutations require workspace:manage (owner-level).
Writes use RLS-scoped PostgreSQL transactions, a visible-agent check, revision
preconditions and append-only audit records. Never trust scope supplied by a client.
The office feature remains unavailable in non-PostgreSQL installations.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import re
from typing import Annotated, Literal
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import office_layout as layout
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import event_runtime, require_permission, tenant_context
from .errors import ApplicationError, ErrorCode, ErrorDetail
from .events import CanonicalEventName, create_event
from .office_layout_history import LAYOUT_ACTIONS
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
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str = Field(min_length=3, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("name_ar")
    @classmethod
    def _ar(cls, value: str | None) -> str | None:
        return _arabic_name(value)


EquipmentKind = Literal[
    "monitor", "display", "whiteboard", "projector", "conference_phone", "video_conference_kit",
    "printer", "server_rack", "network_switch", "workbench", "shelving", "locker",
    "charging_station", "first_aid_kit",
]
RoomColor = Literal["slate", "forest", "brass", "sapphire", "amethyst", "crimson", "teal", "sand"]
TeamDepartment = Literal["sales", "support", "operations", "finance", "tyre_engineering", "marketing", "technology", "research", "legal", "compliance", "security", "hr", "procurement", "custom"]
RoomKind = Literal["office", "meeting", "reception", "workshop", "warehouse", "lounge", "command"]
DoorSide = Literal["north", "south", "east", "west"]
Rotation = Literal[0, 90, 180, 270]
TemplateId = Literal["open_office", "meeting_room", "reception", "warehouse", "workshop", "command_center"]
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _plain_text(value: str | None, *, multiline: bool = False) -> str | None:
    if value is None:
        return None
    if _CONTROL.search(value):
        raise ValueError("Text may not contain control characters")
    return value.strip() if multiline else " ".join(value.split())


def _arabic_name(value: str | None) -> str | None:
    """Optional Arabic display name: one line, no control characters."""
    cleaned = _plain_text(value)
    if value is not None and not cleaned:
        raise ValueError("Name cannot be blank")
    return cleaned


class FloorCreate(StrictBody):
    id: Identifier
    building_id: Identifier
    level: int = Field(ge=-20, le=120)
    name: str = Field(min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    # Metres. New floors default to 60 by 40; the room editor stays inside these bounds.
    width_m: float = Field(default=layout.DEFAULT_FLOOR_WIDTH_M, ge=1, le=1000)
    depth_m: float = Field(default=layout.DEFAULT_FLOOR_DEPTH_M, ge=1, le=1000)

    @field_validator("name_ar")
    @classmethod
    def _ar(cls, value: str | None) -> str | None:
        return _arabic_name(value)


class RoomCreate(StrictBody):
    id: Identifier
    floor_id: Identifier
    name: str = Field(min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    kind: RoomKind
    capacity: int = Field(ge=1, le=5000)

    @field_validator("name_ar")
    @classmethod
    def _ar(cls, value: str | None) -> str | None:
        return _arabic_name(value)


class DeskCreate(StrictBody):
    id: Identifier
    room_id: Identifier
    label: str = Field(min_length=1, max_length=100)


class EquipmentItem(StrictBody):
    kind: EquipmentKind
    quantity: int = Field(ge=1, le=999)
    note: str | None = Field(default=None, max_length=120)

    @field_validator("note")
    @classmethod
    def _clean_note(cls, value: str | None) -> str | None:
        return _plain_text(value) or None


class _Patch(StrictBody):
    """A partial update: absent fields stay as they are, an explicit null clears optional ones."""

    expected_revision: int = Field(ge=1)
    _non_null: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _has_change(self) -> "_Patch":
        given = self.model_fields_set - {"expected_revision"}
        if not given:
            raise ValueError("Send at least one field to change")
        for name in given:
            if getattr(self, name) is None and name in self._non_null:
                raise ValueError(f"{name} cannot be cleared")
        return self

    def changes(self) -> dict[str, Any]:
        return {n: getattr(self, n) for n in self.model_fields_set - {"expected_revision"}}


class BuildingUpdate(_Patch):
    _non_null = ("name", "timezone")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = Field(default=None, min_length=3, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("name_ar")
    @classmethod
    def _ar(cls, value: str | None) -> str | None:
        return _arabic_name(value)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Invalid IANA time zone") from exc
        return value


class FloorUpdate(_Patch):
    _non_null = ("name", "level", "width_m", "depth_m")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    level: int | None = Field(default=None, ge=-20, le=120)
    width_m: float | None = Field(default=None, ge=1, le=1000)
    depth_m: float | None = Field(default=None, ge=1, le=1000)

    @field_validator("name_ar")
    @classmethod
    def _ar(cls, value: str | None) -> str | None:
        return _arabic_name(value)

    @model_validator(mode="after")
    def _size_pair(self) -> "FloorUpdate":
        if ("width_m" in self.model_fields_set) != ("depth_m" in self.model_fields_set):
            raise ValueError("Send width_m and depth_m together")
        return self


class RoomUpdate(_Patch):
    _non_null = ("name", "kind", "capacity", "rotation", "door_side", "description", "notes", "equipment")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: RoomKind | None = None
    capacity: int | None = Field(default=None, ge=1, le=5000)
    rotation: Rotation | None = None
    door_side: DoorSide | None = None
    description: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    color: RoomColor | None = None
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    equipment: list[EquipmentItem] | None = Field(default=None, max_length=20)
    team_department: TeamDepartment | None = None

    @field_validator("name", "name_ar")
    @classmethod
    def _single_line(cls, value: str | None) -> str | None:
        cleaned = _plain_text(value)
        if value is not None and not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    @field_validator("description", "notes")
    @classmethod
    def _multi_line(cls, value: str | None) -> str | None:
        return _plain_text(value, multiline=True)

    @field_validator("equipment")
    @classmethod
    def _unique_kinds(cls, value: list[EquipmentItem] | None) -> list[EquipmentItem] | None:
        if value is not None and len({item.kind for item in value}) != len(value):
            raise ValueError("List each equipment kind once and use quantity for several")
        return value

    def changes(self) -> dict[str, Any]:
        out = super().changes()
        if out.get("equipment") is not None:
            out["equipment"] = [item.model_dump(exclude_none=True) for item in self.equipment or []]
        return out


# Kept for callers that import the old name; a rename is a RoomUpdate with only a name.
RenameRoom = RoomUpdate


class DeskUpdate(_Patch):
    _non_null = ("label",)
    label: str | None = Field(default=None, min_length=1, max_length=100)
    pos_x: float | None = Field(default=None, ge=0, le=1000)
    pos_z: float | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _position_pair(self) -> "DeskUpdate":
        given = self.model_fields_set
        if ("pos_x" in given) != ("pos_z" in given):
            raise ValueError("Send pos_x and pos_z together")
        if "pos_x" in given and (self.pos_x is None) != (self.pos_z is None):
            raise ValueError("Clear both pos_x and pos_z, or set both")
        return self


class RoomGeometryUpdate(StrictBody):
    expected_revision: int = Field(ge=1)
    x: float = Field(ge=-10000,le=10000)
    z: float = Field(ge=-10000,le=10000)
    width: float = Field(ge=1,le=1000)
    depth: float = Field(ge=1,le=1000)


class LayoutRoom(StrictBody):
    id: Identifier
    expected_revision: int = Field(ge=1)
    x: float = Field(ge=-10000, le=10000)
    z: float = Field(ge=-10000, le=10000)
    width: float = Field(ge=1, le=1000)
    depth: float = Field(ge=1, le=1000)
    rotation: Rotation | None = None
    door_side: DoorSide | None = None


class LayoutDesk(StrictBody):
    id: Identifier
    expected_revision: int = Field(ge=1)
    pos_x: float = Field(ge=0, le=1000)
    pos_z: float = Field(ge=0, le=1000)


class FloorLayout(StrictBody):
    """Every room and desk to move, saved together or not at all."""

    rooms: list[LayoutRoom] = Field(default_factory=list, max_length=500)
    desks: list[LayoutDesk] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def _valid(self) -> "FloorLayout":
        if not self.rooms and not self.desks:
            raise ValueError("Send at least one room or desk")
        for items, label in ((self.rooms, "room"), (self.desks, "desk")):
            if len({item.id for item in items}) != len(items):
                raise ValueError(f"Each {label} may appear once")
        return self


class RoomCopy(StrictBody):
    id: Identifier
    name: str | None = Field(default=None, min_length=1, max_length=100)
    floor_id: Identifier | None = None
    x: float | None = Field(default=None, ge=-10000, le=10000)
    z: float | None = Field(default=None, ge=-10000, le=10000)
    copy_desks: bool = False

    @model_validator(mode="after")
    def _pair(self) -> "RoomCopy":
        if (self.x is None) != (self.z is None):
            raise ValueError("Send x and z together")
        return self


class RoomFromTemplate(StrictBody):
    id: Identifier
    floor_id: Identifier
    template_id: TemplateId
    name: str | None = Field(default=None, min_length=1, max_length=100)
    x: float | None = Field(default=None, ge=-10000, le=10000)
    z: float | None = Field(default=None, ge=-10000, le=10000)
    with_desks: bool = False

    @model_validator(mode="after")
    def _pair(self) -> "RoomFromTemplate":
        if (self.x is None) != (self.z is None):
            raise ValueError("Send x and z together")
        return self


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
        store.pending_events = []  # type: ignore[attr-defined]
        store.touched_floors = {}  # type: ignore[attr-defined]
        yield store
        # Still inside the unit of work: the layout revision commits or rolls back with the change itself.
        for floor_id, action in store.touched_floors.items():  # type: ignore[attr-defined]
            store.record_floor_revision(context, floor_id, action)
    # Reached only after the unit of work committed: now other open browsers hear of it.
    event_runtime.after_commit(store.pending_events)  # type: ignore[attr-defined]


def _error(exc: SpaceStoreError) -> ApplicationError:
    status_code = {"invalid": 422, "not_found": 404, "conflict": 409, "restricted": 409}.get(exc.code, 409)
    code = {422: ErrorCode.VALIDATION_ERROR, 404: ErrorCode.NOT_FOUND}.get(status_code, ErrorCode.CONFLICT)
    details = [ErrorDetail(location=loc or None, message=msg, type=kind) for loc, kind, msg in exc.issues]
    if not details and exc.reason:
        details = [ErrorDetail(message=str(exc), type=exc.reason)]
    return ApplicationError(code, str(exc), status_code=status_code, details=details)


def _plain(value: Any) -> Any:
    """JSON safe copy for audit values: no scope columns, floats for decimals."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k not in ("tenant_id", "workspace_id")}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


_AUDIT_FIELDS = {
    "buildings": ("name", "name_ar", "timezone", "latitude", "longitude"),
    "floors": ("building_id", "level", "name", "name_ar", "width_m", "depth_m"),
    "rooms": ("floor_id", "name", "kind", "capacity", "scene_x", "scene_z", "scene_width", "scene_depth",
              "rotation", "door_side", "description", "notes", "color", "name_ar", "equipment", "team_department"),
    "desks": ("room_id", "label", "agent_id", "pos_x", "pos_z"),
}


def _snap(kind: str, row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return _plain({"id": row["id"], "revision": row["revision"], **{f: row.get(f) for f in _AUDIT_FIELDS[kind]}})


def _audit(
    store: SqlOfficeSpaceStore, context: TenantContext, action: str, target: str,
    *, kind: str | None = None, before: Any = None, after: Any = None,
    floor_id: str | None = None, ids: list[str] | None = None,
) -> None:
    """Audit record with before and after values, plus the layout-changed event.

    Both are written in the request's transaction. The event carries ids and revisions
    only; clients re-read the records they care about.
    """
    from .db.repository import SqlAlchemyRepository

    metadata: dict[str, Any] = {"office_resource": target}
    if kind:
        metadata["resource_kind"] = kind
    if floor_id:
        metadata["floor_id"] = floor_id
    if before is not None:
        metadata["before"] = before
    if after is not None:
        metadata["after"] = after
    if action in LAYOUT_ACTIONS:
        touched = floor_id
        if touched is None and kind == "desks":
            touched = store.floor_id_of(context, "rooms", (after or before or {}).get("room_id", ""))
        if touched is not None and hasattr(store, "touched_floors"):
            store.touched_floors[touched] = action  # type: ignore[attr-defined]
    repository = SqlAlchemyRepository(store.session)
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action=action, target=target, outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(), metadata=metadata,
        )
    )
    shown = (ids or [target])[:50]
    payload: dict[str, Any] = {"action": action, "resource_kind": kind, "resource_ids": shown,
                               "resource_count": len(ids or [target])}
    if floor_id:
        payload["floor_id"] = floor_id
    event = create_event(CanonicalEventName.OFFICE_LAYOUT_CHANGED, context, floor_id or target, payload).event
    store.pending_events.append(repository.record_event(event))  # type: ignore[attr-defined]


def _create(
    context: TenantContext, kind: ItemKind, body: StrictBody,
    key: str | None, response: Response,
) -> dict:
    with _store(context) as store:
        try:
            item_id = body.id
            fields = body.model_dump(exclude={"id"})
            if fields.get("name_ar") is None:
                fields.pop("name_ar", None)  # absent means NULL; also keeps old idempotency hashes valid
            if kind == "desks":
                fields["agent_id"] = None
            row, created = store.create_with_key(context, kind, item_id, fields, key)
            if created:
                _audit(store, context, f"office.{kind[:-1]}.create", item_id, kind=kind,
                       after=_snap(kind, row), floor_id=_floor_of(kind, row))
            else:
                response.status_code = status.HTTP_200_OK
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


def _floor_of(kind: str, row: dict[str, Any]) -> str | None:
    if kind == "floors":
        return row["id"]
    if kind == "rooms":
        return row["floor_id"]
    return None


@router.get("/templates")
def list_templates(context: TenantContext = Depends(tenant_context)) -> dict:
    """The fixed room templates, sizes in metres. Same for every workspace."""
    _readable(context)
    return {
        "items": [
            {
                "id": t.id, "name": t.name, "name_ar": t.name_ar, "kind": t.kind, "capacity": t.capacity,
                "width": t.width, "depth": t.depth, "door_side": t.door_side, "color": t.color,
                "equipment": [{"kind": k, "quantity": q} for k, q in t.equipment], "desks": t.desks,
            }
            for t in layout.TEMPLATES
        ],
        "equipment_kinds": list(layout.EQUIPMENT_KINDS),
        "colors": list(layout.ROOM_COLORS),
        "departments": list(layout.TEAM_DEPARTMENTS),
        "units": "metres",
    }


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


@router.get("/rooms/{room_id}/team")
def room_team(room_id: Identifier, context: TenantContext = Depends(tenant_context)) -> dict:
    """Counts of the linked department's real records; null when no department is linked."""
    _readable(context)
    with _store(context) as store:
        try:
            return store.team_summary(context, room_id)
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/buildings", status_code=status.HTTP_201_CREATED)
def create_building(body: BuildingCreate, response: Response, idempotency_key: CreateKey = None, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
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


def _patch(context: TenantContext, kind: ItemKind, item_id: str, body: _Patch, action: str) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            before, after = store.patch(context, kind, item_id, body.expected_revision, body.changes())
            _audit(store, context, action, item_id, kind=kind, before=_snap(kind, before), after=_snap(kind, after),
                   floor_id=_floor_of(kind, after))
            return after
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.patch("/buildings/{building_id}")
def update_building(building_id: Identifier, body: BuildingUpdate, context: TenantContext = Depends(tenant_context)) -> dict:
    return _patch(context, "buildings", building_id, body, "office.building.update")


@router.patch("/floors/{floor_id}")
def update_floor(floor_id: Identifier, body: FloorUpdate, context: TenantContext = Depends(tenant_context)) -> dict:
    return _patch(context, "floors", floor_id, body, "office.floor.update")


@router.patch("/rooms/{room_id}")
def update_room(room_id: Identifier, body: RoomUpdate, context: TenantContext = Depends(tenant_context)) -> dict:
    """Name, type, capacity, rotation, door side, details, equipment and team link."""
    action = "office.room.rename" if set(body.changes()) == {"name"} else "office.room.update"
    return _patch(context, "rooms", room_id, body, action)


@router.patch("/desks/{desk_id}")
def update_desk(desk_id: Identifier, body: DeskUpdate, context: TenantContext = Depends(tenant_context)) -> dict:
    """Rename a desk or place it inside its room (metres from the room's north west corner)."""
    return _patch(context, "desks", desk_id, body, "office.desk.update")


@router.patch("/desks/{desk_id}/assignment")
def assign_desk(desk_id: Identifier, body: AssignDesk, context: TenantContext = Depends(tenant_context)) -> dict:
    _writable(context)
    with _store(context) as store:
        try:
            previous = store.get(context, "desks", desk_id)
            row = store.assignment(context, desk_id, body.agent_id, body.expected_revision)
            _audit(store, context, "office.desk.assignment", desk_id, kind="desks",
                   before=_snap("desks", previous), after=_snap("desks", row))
            return row
        except SpaceStoreError as exc:
            raise _error(exc) from exc


def _delete(context: TenantContext, kind: ItemKind, item_id: str, revision: int) -> None:
    _writable(context)
    with _store(context) as store:
        try:
            before = getattr(store, f"delete_{kind[:-1]}")(context, item_id, revision)
            _audit(store, context, f"office.{kind[:-1]}.delete", item_id, kind=kind,
                   before=_snap(kind, before), floor_id=_floor_of(kind, before))
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.delete("/buildings/{building_id}", status_code=204)
def delete_building(building_id: Identifier, expected_revision: int = Query(ge=1),
                    context: TenantContext = Depends(tenant_context)) -> None:
    """Only an empty building (no floors); the 409 lists the floors in the way."""
    _delete(context, "buildings", building_id, expected_revision)


@router.delete("/floors/{floor_id}", status_code=204)
def delete_floor(floor_id: Identifier, expected_revision: int = Query(ge=1),
                 context: TenantContext = Depends(tenant_context)) -> None:
    """Only an empty floor (no rooms); the 409 lists the rooms and desks in the way."""
    _delete(context, "floors", floor_id, expected_revision)


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(
    room_id: Identifier,
    expected_revision: int = Query(ge=1),
    context: TenantContext = Depends(tenant_context),
) -> None:
    _delete(context, "rooms", room_id, expected_revision)


@router.delete("/desks/{desk_id}", status_code=204)
def delete_desk(
    desk_id: Identifier,
    expected_revision: int = Query(ge=1),
    context: TenantContext = Depends(tenant_context),
) -> None:
    _writable(context)
    with _store(context) as store:
        try:
            before = store.get(context, "desks", desk_id)
            store.delete_desk(context, desk_id, expected_revision)
            _audit(store, context, "office.desk.delete", desk_id, kind="desks", before=_snap("desks", before))
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.patch("/rooms/{room_id}/geometry")
def update_room_geometry(room_id: Identifier, body: RoomGeometryUpdate,
                         context: TenantContext = Depends(tenant_context)) -> dict:
    """Audited, optimistic room placement with floor locking, bounds and overlap checks."""
    _writable(context)
    with _store(context) as store:
        try:
            before, after = store.update_room_geometry(
                context, room_id, body.expected_revision, body.x, body.z, body.width, body.depth)
            _audit(store, context, "office.room.geometry", room_id, kind="rooms",
                   before=_snap("rooms", before), after=_snap("rooms", after), floor_id=after["floor_id"])
            return after
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.put("/floors/{floor_id}/layout")
def save_floor_layout(floor_id: Identifier, body: FloorLayout,
                      context: TenantContext = Depends(tenant_context)) -> dict:
    """Move many rooms and desks at once. Valid as a whole or refused as a whole (swaps work)."""
    _writable(context)
    with _store(context) as store:
        try:
            result = store.save_floor_layout(
                context, floor_id, [r.model_dump() for r in body.rooms], [d.model_dump() for d in body.desks])
            ids = [r["id"] for r in result["rooms"]] + [d["id"] for d in result["desks"]]
            _audit(
                store, context, "office.floor.layout", floor_id, kind="floors", floor_id=floor_id, ids=ids,
                before={"rooms": [_snap("rooms", r) for r in result["before_rooms"]],
                        "desks": [_snap("desks", d) for d in result["before_desks"]]},
                after={"rooms": [_snap("rooms", r) for r in result["rooms"]],
                       "desks": [_snap("desks", d) for d in result["desks"]]},
            )
            return {"floor_id": floor_id, "rooms": result["rooms"], "desks": result["desks"]}
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/rooms/from-template", status_code=status.HTTP_201_CREATED)
def create_room_from_template(body: RoomFromTemplate, context: TenantContext = Depends(tenant_context)) -> dict:
    """Create a placed room (and optionally its desks) from a fixed template."""
    _writable(context)
    template = layout.template_by_id(body.template_id)
    if template is None:
        raise HTTPException(422, "Unknown template")
    with _store(context) as store:
        try:
            room, desks = store.create_room_from_template(
                context, template, body.id, body.floor_id, body.name, body.x, body.z, body.with_desks)
            _audit(store, context, "office.room.create", body.id, kind="rooms", floor_id=room["floor_id"],
                   after={**_snap("rooms", room), "template_id": template.id, "desks": [d["id"] for d in desks]})
            return {"room": room, "desks": desks}
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/rooms/{room_id}/copy", status_code=status.HTTP_201_CREATED)
def copy_room(room_id: Identifier, body: RoomCopy, context: TenantContext = Depends(tenant_context)) -> dict:
    """Copy a room's type, size, details and equipment (desks only on request, never agents)."""
    _writable(context)
    with _store(context) as store:
        try:
            room, desks = store.copy_room(context, room_id, body.id, body.name, body.floor_id,
                                          body.x, body.z, body.copy_desks)
            _audit(store, context, "office.room.copy", body.id, kind="rooms", floor_id=room["floor_id"],
                   after={**_snap("rooms", room), "copied_from": room_id, "desks": [d["id"] for d in desks]})
            return {"room": room, "desks": desks}
        except SpaceStoreError as exc:
            raise _error(exc) from exc
