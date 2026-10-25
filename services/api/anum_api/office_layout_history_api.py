"""Layout revision history, restore and floor/building templates (docs/ANUM_MINE_LAYOUT_HISTORY.md).

Reads need ``task:read``. Restore and every template write need ``workspace:manage`` and, for restore, a click:
the person must have been shown the revision (``payload_hash``) and a typed, voice or assistant request can
never confirm it. Restore creates a NEW revision, is idempotent, and refuses to remove a desk with an agent
unless the exact list shown is confirmed. Responses never carry exception text.
"""
from __future__ import annotations

from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_space_api import Identifier, _audit, _error, _plain_text, _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext

router = APIRouter(prefix="/api/v1/office-layout", tags=["office layout history"])
Name = Annotated[str, Field(min_length=1, max_length=100)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RestoreBody(_Strict):
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_head: int = Field(ge=1)
    confirm_unassign: list[Identifier] = Field(default_factory=list, max_length=2000)
    input_mode: Literal["click", "typed", "voice"] = "typed"


class TemplateFromFloor(_Strict):
    id: Identifier
    floor_id: Identifier
    name: Name


class TemplateFromBuilding(_Strict):
    id: Identifier
    building_id: Identifier
    name: Name


class TemplateRename(_Strict):
    name: Name
    expected_revision: int = Field(ge=1)


class FloorFromTemplate(_Strict):
    template_id: Identifier
    id: Identifier
    building_id: Identifier
    level: int = Field(ge=-20, le=120)
    name: Name | None = None


class BuildingFromTemplate(_Strict):
    template_id: Identifier
    id: Identifier
    name: Name
    timezone: str = Field(min_length=3, max_length=80)


def _read(context: TenantContext) -> None:
    require_permission(context, Permission.TASK_READ)


def _write(context: TenantContext) -> None:
    require_permission(context, Permission.WORKSPACE_MANAGE)


def _clean(name: str) -> str:
    cleaned = _plain_text(name)
    if not cleaned:
        raise HTTPException(422, "Name cannot be blank")
    return cleaned


# ---------------------------------------------------------------------- history
@router.get("/floors/{floor_id}/revisions")
def list_revisions(floor_id: Identifier, before: int | None = Query(default=None, ge=1),
                   limit: int = Query(default=30, ge=1, le=100),
                   context: TenantContext = Depends(tenant_context)) -> dict:
    _read(context)
    with _store(context) as store:
        try:
            out = store.list_revisions(context, floor_id, before, limit)
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    out["can_restore"] = has_permission(context, Permission.WORKSPACE_MANAGE)
    return out


@router.get("/floors/{floor_id}/revisions/{revision_no}")
def get_revision(floor_id: Identifier, revision_no: int,
                 against: Literal["current", "previous"] = "current",
                 context: TenantContext = Depends(tenant_context)) -> dict:
    """One revision with a diff: against the floor as it is now (what a restore does) or against its predecessor."""
    _read(context)
    if revision_no < 1:
        raise HTTPException(404, "Revision not found")
    with _store(context) as store:
        try:
            out = store.revision_detail(context, floor_id, revision_no, against, context.user_id)
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    out["can_restore"] = has_permission(context, Permission.WORKSPACE_MANAGE)
    return out


@router.post("/floors/{floor_id}/revisions/{revision_no}/restore")
def restore_revision(floor_id: Identifier, revision_no: int, body: RestoreBody,
                     context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    if body.input_mode != "click":
        raise HTTPException(403, "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    if revision_no < 1:
        raise HTTPException(404, "Revision not found")
    with _store(context) as store:
        try:
            result = store.restore_floor_revision(
                context, floor_id, revision_no, payload_hash=body.payload_hash,
                expected_head=body.expected_head, confirm_unassign=list(body.confirm_unassign))
            if result["restored"] and not result["replayed"]:
                _audit(
                    store, context, "office.floor.restore", floor_id, kind="floors", floor_id=floor_id,
                    before={"rooms": len(result["before"]["rooms"]), "desks": len(result["before"]["desks"])},
                    after={"restored_from": revision_no, "new_revision": result["revision_no"],
                           "payload_hash": body.payload_hash, "input_mode": "click",
                           "rooms": len(result["after"]["rooms"]), "desks": len(result["after"]["desks"]),
                           "summary": result["summary"],
                           "unassigned_desks": [u["desk_id"] for u in result["unassigned"]]},
                )
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    return {
        "floor_id": floor_id, "restored": result["restored"], "replayed": result["replayed"], "noop": result["noop"],
        "revision_no": result["revision_no"], "restored_from": result["restored_from"],
        "unassigned": [{"desk_id": u["desk_id"], "label": u["label"], "room": u["room"]} for u in result["unassigned"]],
        "summary": result["summary"],
    }


# ---------------------------------------------------------------------- templates
@router.get("/templates")
def list_templates(kind: Literal["floor", "building"] | None = None,
                   context: TenantContext = Depends(tenant_context)) -> dict:
    _read(context)
    with _store(context) as store:
        items = store.list_templates(context, kind)
    return {"items": items, "can_manage": has_permission(context, Permission.WORKSPACE_MANAGE)}


@router.post("/templates/from-floor", status_code=status.HTTP_201_CREATED)
def save_floor_template(body: TemplateFromFloor, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    with _store(context) as store:
        try:
            row, skipped = store.save_floor_template(context, body.id, _clean(body.name), body.floor_id)
            _audit(store, context, "office.template.create", body.id, after={"kind": "floor", "name": row["name"], "floor_id": body.floor_id, **row["summary"]})
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    return {**row, "skipped_unplaced_rooms": skipped}


@router.post("/templates/from-building", status_code=status.HTTP_201_CREATED)
def save_building_template(body: TemplateFromBuilding, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    with _store(context) as store:
        try:
            row, skipped = store.save_building_template(context, body.id, _clean(body.name), body.building_id)
            _audit(store, context, "office.template.create", body.id, after={"kind": "building", "name": row["name"], "building_id": body.building_id, **row["summary"]})
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    return {**row, "skipped_unplaced_rooms": skipped}


@router.patch("/templates/{template_id}")
def rename_template(template_id: Identifier, body: TemplateRename, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    with _store(context) as store:
        try:
            row = store.rename_template(context, template_id, _clean(body.name), body.expected_revision)
            _audit(store, context, "office.template.rename", template_id, after={"name": row["name"]})
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    return row


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: Identifier, expected_revision: int = Query(ge=1),
                    context: TenantContext = Depends(tenant_context)) -> None:
    _write(context)
    with _store(context) as store:
        try:
            before = store.delete_template(context, template_id, expected_revision)
            _audit(store, context, "office.template.delete", template_id, before={"kind": before["kind"], "name": before["name"]})
        except SpaceStoreError as exc:
            raise _error(exc) from exc


@router.post("/floors/from-template", status_code=status.HTTP_201_CREATED)
def floor_from_template(body: FloorFromTemplate, context: TenantContext = Depends(tenant_context)) -> dict:
    """A new floor with the template's rooms and empty desks. No agent is ever copied."""
    _write(context)
    with _store(context) as store:
        try:
            tpl = store.get_template(context, body.template_id, "floor")
            if store.get(context, "buildings", body.building_id) is None:
                raise SpaceStoreError("not_found", "Building not found in this workspace")
            definition = tpl["definition"]
            built = store.build_floor_from_definition(context, definition, body.id, body.building_id, body.level,
                                                      body.name or definition["name"])
            _audit(store, context, "office.floor.create_from_template", body.id, kind="floors",
                   after={**_snap("floors", built["floor"]), "template_id": tpl["id"], "rooms": len(built["room_ids"]), "desks": built["desks"]},
                   floor_id=body.id, ids=[body.id, *built["room_ids"]])
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    return {"floor": built["floor"], "room_ids": built["room_ids"], "desk_count": built["desks"]}


@router.post("/buildings/from-template", status_code=status.HTTP_201_CREATED)
def building_from_template(body: BuildingFromTemplate, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "Invalid IANA time zone") from exc
    with _store(context) as store:
        try:
            tpl = store.get_template(context, body.template_id, "building")
            building = store.create(context, "buildings", body.id, {"name": _clean(body.name), "timezone": body.timezone,
                                                                    "latitude": None, "longitude": None})
            _audit(store, context, "office.building.create", body.id, kind="buildings", after=_snap("buildings", building),
                   ids=[body.id])
            floor_ids, room_total, desk_total = [], 0, 0
            for index, definition in enumerate(tpl["definition"]["floors"], start=1):
                floor_id = f"{body.id[:70]}-f{index}"
                built = store.build_floor_from_definition(context, definition, floor_id, body.id, definition["level"], definition["name"])
                floor_ids.append(floor_id)
                room_total += len(built["room_ids"])
                desk_total += built["desks"]
                _audit(store, context, "office.floor.create_from_template", floor_id, kind="floors",
                       after={**_snap("floors", built["floor"]), "template_id": tpl["id"], "rooms": len(built["room_ids"]), "desks": built["desks"]},
                       floor_id=floor_id, ids=[floor_id, *built["room_ids"]])
        except SpaceStoreError as exc:
            raise _error(exc) from exc
    return {"building": building, "floor_ids": floor_ids, "room_count": room_total, "desk_count": desk_total}
