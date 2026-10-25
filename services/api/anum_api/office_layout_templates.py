"""Floor and building templates for the office builder (docs/ANUM_MINE_LAYOUT_HISTORY.md#templates).

A template is a named, workspace scoped copy of a floor's rooms (position, size, rotation, door, equipment,
department) and desk positions, or of a whole building's floors. It never holds an agent: a floor made from it
has empty desks. The definition is plain JSON validated by the same builder rules when it is applied.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import office_layout as layout
from .office_space_errors import SpaceStoreError
from .schemas import TenantContext

MAX_TEMPLATES: Final = 100
DEFINITION_VERSION: Final = 1
_ROOM_VALUE_FIELDS: Final = ("name", "kind", "capacity", "rotation", "door_side", "description", "notes", "color",
                             "name_ar", "equipment", "team_department")
TEMPLATE_NAME_TAKEN: Final = "TEMPLATE_NAME_TAKEN"
TEMPLATE_LIMIT: Final = "TEMPLATE_LIMIT"


def capture_floor(floor: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """(definition, skipped): rooms without a place on the plan cannot be templated and are counted."""
    desks_by_room: dict[str, list[dict[str, Any]]] = {}
    for d in snapshot["desks"]:
        desks_by_room.setdefault(d["room_id"], []).append(d)
    rooms, skipped = [], 0
    for r in snapshot["rooms"]:
        if r["scene_x"] is None:
            skipped += 1
            continue
        rooms.append({
            **{f: r[f] for f in _ROOM_VALUE_FIELDS},
            "x": r["scene_x"], "z": r["scene_z"], "width": r["scene_width"], "depth": r["scene_depth"],
            "desks": [{"label": d["label"], "x": d["pos_x"], "z": d["pos_z"]} for d in desks_by_room.get(r["id"], [])],
        })
    return {
        "level": floor["level"], "name": floor["name"],
        "width_m": snapshot["floor"]["width_m"] or layout.DEFAULT_FLOOR_WIDTH_M,
        "depth_m": snapshot["floor"]["depth_m"] or layout.DEFAULT_FLOOR_DEPTH_M,
        "rooms": rooms,
    }, skipped


def template_summary(kind: str, definition: Mapping[str, Any]) -> dict[str, int]:
    floors = [definition] if kind == "floor" else list(definition.get("floors", []))
    rooms = sum(len(f.get("rooms", [])) for f in floors)
    desks = sum(len(r.get("desks", [])) for f in floors for r in f.get("rooms", []))
    return {"floors": len(floors), "rooms": rooms, "desks": desks}


def template_view(row: Mapping[str, Any], with_definition: bool = False) -> dict[str, Any]:
    out = {
        "id": row["id"], "kind": row["kind"], "name": row["name"], "revision": row["revision"],
        "created_by": row["created_by"],
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        "summary": template_summary(row["kind"], row["definition"]),
    }
    if with_definition:
        out["definition"] = row["definition"]
    return out


class TemplateMixin:
    session: Any

    def list_templates(self, context: TenantContext, kind: str | None) -> list[dict[str, Any]]:
        rows = self.session.execute(text("""
            select id, kind, name, revision, definition, created_by, created_at from office_layout_templates
            where tenant_id = :tenant and workspace_id = :workspace and (cast(:kind as text) is null or kind = :kind)
            order by lower(name), id limit 200
        """), self._params(context, kind=kind)).mappings().all()  # type: ignore[attr-defined]
        return [template_view(dict(r)) for r in rows]

    def get_template(self, context: TenantContext, template_id: str, expected_kind: str | None = None) -> dict[str, Any]:
        row = self.session.execute(text("""
            select id, kind, name, revision, definition, created_by, created_at from office_layout_templates
            where tenant_id = :tenant and workspace_id = :workspace and id = :id
        """), self._params(context, id=template_id)).mappings().first()  # type: ignore[attr-defined]
        if row is None or (expected_kind is not None and row["kind"] != expected_kind):
            raise SpaceStoreError("not_found", "Template not found in this workspace")
        return dict(row)

    def _insert_template(self, context: TenantContext, template_id: str, kind: str, name: str,
                         definition: Mapping[str, Any]) -> dict[str, Any]:
        count = self.session.execute(text("""
            select count(*) from office_layout_templates where tenant_id = :tenant and workspace_id = :workspace
        """), self._params(context)).scalar_one()  # type: ignore[attr-defined]
        if count >= MAX_TEMPLATES:
            raise SpaceStoreError("restricted", f"A workspace can keep {MAX_TEMPLATES} templates. Delete one first",
                                  reason=TEMPLATE_LIMIT)
        try:
            with self.session.begin_nested():
                self.session.execute(text("""
                    insert into office_layout_templates (id, tenant_id, workspace_id, kind, name, definition, created_by)
                    values (:id, :tenant, :workspace, :kind, :name, cast(:definition as jsonb), :actor)
                """), self._params(context, id=template_id, kind=kind, name=name,  # type: ignore[attr-defined]
                                   definition=json.dumps({"v": DEFINITION_VERSION, **definition}), actor=context.user_id))
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_office_layout_template_name":
                raise SpaceStoreError("conflict", "A template of this kind already has that name", reason=TEMPLATE_NAME_TAKEN) from exc
            raise SpaceStoreError("conflict", "A template with that id already exists") from exc
        return template_view(self.get_template(context, template_id))

    def save_floor_template(self, context: TenantContext, template_id: str, name: str, floor_id: str) -> tuple[dict[str, Any], int]:
        floor = self.get(context, "floors", floor_id)  # type: ignore[attr-defined]
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        definition, skipped = capture_floor(floor, self._current_snapshot(context, floor))  # type: ignore[attr-defined]
        return self._insert_template(context, template_id, "floor", name, definition), skipped

    def save_building_template(self, context: TenantContext, template_id: str, name: str, building_id: str) -> tuple[dict[str, Any], int]:
        if self.get(context, "buildings", building_id) is None:  # type: ignore[attr-defined]
            raise SpaceStoreError("not_found", "Building not found in this workspace")
        floors = self.session.execute(text("""
            select id from office_floors where tenant_id = :tenant and workspace_id = :workspace and building_id = :b
            order by level
        """), self._params(context, b=building_id)).scalars().all()  # type: ignore[attr-defined]
        if not floors:
            raise SpaceStoreError("conflict", "This building has no floors to save as a template", reason="BUILDING_EMPTY")
        if len(floors) > 120:
            raise SpaceStoreError("conflict", "This building has too many floors for a template")
        defs, skipped = [], 0
        for fid in floors:
            floor = self.get(context, "floors", fid)  # type: ignore[attr-defined]
            d, s = capture_floor(floor, self._current_snapshot(context, floor))  # type: ignore[attr-defined]
            defs.append(d)
            skipped += s
        return self._insert_template(context, template_id, "building", name, {"floors": defs}), skipped

    def rename_template(self, context: TenantContext, template_id: str, name: str, revision: int) -> dict[str, Any]:
        try:
            with self.session.begin_nested():
                row = self.session.execute(text("""
                    update office_layout_templates set name = :name, revision = revision + 1, updated_at = now()
                    where tenant_id = :tenant and workspace_id = :workspace and id = :id and revision = :rev returning id
                """), self._params(context, id=template_id, name=name, rev=revision)).first()  # type: ignore[attr-defined]
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "A template of this kind already has that name", reason=TEMPLATE_NAME_TAKEN) from exc
        if row is None:
            self.get_template(context, template_id)
            raise SpaceStoreError("conflict", "Office layout changed. Reload before retrying", reason=layout.STALE_REVISION)
        return template_view(self.get_template(context, template_id))

    def delete_template(self, context: TenantContext, template_id: str, revision: int) -> dict[str, Any]:
        before = self.get_template(context, template_id)
        row = self.session.execute(text("""
            delete from office_layout_templates
            where tenant_id = :tenant and workspace_id = :workspace and id = :id and revision = :rev returning id
        """), self._params(context, id=template_id, rev=revision)).first()  # type: ignore[attr-defined]
        if row is None:
            raise SpaceStoreError("conflict", "Office layout changed. Reload before retrying", reason=layout.STALE_REVISION)
        return before

    def build_floor_from_definition(self, context: TenantContext, definition: Mapping[str, Any], floor_id: str,
                                    building_id: str, level: int, name: str) -> dict[str, Any]:
        """Create the floor, its rooms and EMPTY desks through the builder's own placement checks."""
        try:
            floor = self.create(context, "floors", floor_id, {  # type: ignore[attr-defined]
                "building_id": building_id, "level": level, "name": name,
                "width_m": float(definition["width_m"]), "depth_m": float(definition["depth_m"])})
            room_ids: list[str] = []
            desk_count = 0
            for index, room in enumerate(definition["rooms"], start=1):
                room_id = f"{floor_id[:60]}-r{index}"
                desks = [(f"{room_id[:72]}-d{n}", d["label"], (d["x"], d["z"]) if d["x"] is not None else None)
                         for n, d in enumerate(room["desks"], start=1)]
                self.create_planned_room(  # type: ignore[attr-defined]
                    context, floor_id, room_id, {f: room[f] for f in _ROOM_VALUE_FIELDS},
                    room["x"], room["z"], room["width"], room["depth"], desks)
                room_ids.append(room_id)
                desk_count += len(desks)
        except (KeyError, TypeError, ValueError) as exc:
            raise SpaceStoreError("invalid", "This template is damaged and cannot be applied", reason="TEMPLATE_DAMAGED") from exc
        return {"floor": floor, "room_ids": room_ids, "desks": desk_count}
