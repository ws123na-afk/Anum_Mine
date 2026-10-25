"""Workspace-scoped persistent office configuration repository.

Caller must supply a Session with tenant, workspace and user RLS context already set.
Every query repeats the scope predicate defensively. Updates use optimistic revisions.
No agent activity or private agent profile data are fabricated in this module.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from sqlalchemy import column, delete, func, insert, select, table, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from . import office_layout as layout
from .schemas import TenantContext


class SpaceStoreError(Exception):
    """A refused office change.

    ``code`` picks the HTTP status (invalid 422, not_found 404, conflict and restricted
    409). ``reason`` is a stable machine code and ``issues`` lists each problem as
    (location, machine code, message), so a client can mark the exact rooms involved.
    """

    def __init__(self, code: str, message: str, *, reason: str | None = None,
                 issues: tuple[tuple[str, str, str], ...] = ()):
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.issues = tuple(issues)


_TABLES = {
    "buildings": "office_buildings",
    "floors": "office_floors",
    "rooms": "office_rooms",
    "desks": "office_desks",
}
_PARENT = {"floors": ("building_id", "buildings"), "rooms": ("floor_id", "floors"), "desks": ("room_id", "rooms")}
_COLUMNS = {
    "buildings": ("name", "timezone", "latitude", "longitude"),
    "floors": ("building_id", "level", "name", "width_m", "depth_m"),
    "rooms": ("floor_id", "name", "kind", "capacity"),
    "desks": ("room_id", "label", "agent_id"),
}
# Columns that exist on a read but are not part of a create request.
_EXTRA_COLUMNS = {
    "buildings": (),
    "floors": (),
    "rooms": ("scene_x", "scene_z", "scene_width", "scene_depth", "rotation", "door_side", "description",
              "notes", "color", "name_ar", "equipment", "team_department"),
    "desks": ("pos_x", "pos_z"),
}
# Columns a PATCH may change, per resource (the API models are the first filter).
_PATCHABLE = {
    "buildings": ("name", "timezone", "latitude", "longitude"),
    "floors": ("name", "level", "width_m", "depth_m"),
    "rooms": ("name", "kind", "capacity", "rotation", "door_side", "description", "notes", "color",
              "name_ar", "equipment", "team_department"),
    "desks": ("label", "pos_x", "pos_z"),
}
_ROOM_COPY_COLUMNS = ("kind", "capacity", "rotation", "door_side", "description", "notes", "color",
                      "name_ar", "equipment", "team_department")


def _typed(kind: str, field: str):
    return column(field, JSONB) if field == "equipment" else column(field)


_SQL_TABLES = {
    kind: table(name, *(_typed(kind, field) for field in (
        "id", "tenant_id", "workspace_id", "revision", "created_by", "created_at", "updated_at",
        *_COLUMNS[kind], *_EXTRA_COLUMNS[kind],
    )))
    for kind, name in _TABLES.items()
}


_STALE = "Office layout changed. Reload before retrying"
_UNIQUE_REASONS = {
    "uq_office_room_name": ("ROOM_NAME_TAKEN", "A room already has that name on this floor"),
    "uq_office_floor_level": ("FLOOR_LEVEL_TAKEN", "This building already has a floor at that level"),
    "uq_office_desk_label": ("DESK_LABEL_TAKEN", "A desk in this room already has that label"),
    "uq_office_agent_desk": ("AGENT_ALREADY_SEATED", "This agent already occupies another desk"),
}


def _conflict_from(exc: IntegrityError, fallback: str) -> SpaceStoreError:
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    reason, message = _UNIQUE_REASONS.get(name or "", (None, fallback))
    return SpaceStoreError("conflict", message, reason=reason)


def _rect(row: Mapping[str, Any]) -> layout.Rect:
    return layout.Rect(row["id"], row["scene_x"], row["scene_z"], row["scene_width"], row["scene_depth"])


def _bounds(floor: Mapping[str, Any]) -> tuple[float, float] | None:
    if floor.get("width_m") is None:
        return None
    return (float(floor["width_m"]), float(floor["depth_m"]))


def _refuse(issues: list[layout.LayoutIssue], message: str) -> SpaceStoreError:
    return SpaceStoreError(
        "invalid", message, reason=issues[0].code,
        issues=tuple((i.location, i.code, i.message) for i in issues),
    )


class SqlOfficeSpaceStore:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _table(kind: str) -> str:
        try:
            return _TABLES[kind]
        except KeyError as exc:
            raise SpaceStoreError("invalid", "Unknown office resource") from exc

    @staticmethod
    def _params(context: TenantContext, **kwargs: Any) -> dict[str, Any]:
        return {"tenant": context.tenant_id, "workspace": context.workspace_id, **kwargs}

    def list(
        self, context: TenantContext, kind: str,
        parent_id: str | None = None, cursor: str | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise SpaceStoreError("invalid", "Page size must be between 1 and 100")
        self._table(kind)
        office_table = _SQL_TABLES[kind]
        parent = _PARENT.get(kind)
        query = select(office_table).where(
            office_table.c.tenant_id == context.tenant_id,
            office_table.c.workspace_id == context.workspace_id,
        )
        if parent and parent_id is not None:
            query = query.where(office_table.c[parent[0]] == parent_id)
        if cursor:
            query = query.where(office_table.c.id > cursor)
        query = query.order_by(office_table.c.id).limit(limit + 1)
        records = [dict(row) for row in self.session.execute(query).mappings().all()]
        has_next = len(records) > limit
        records = records[:limit]
        if kind == "desks":
            self._redact_invisible_agents(context, records)
        return {"items": records, "next_cursor": records[-1]["id"] if has_next else None}

    def get(self, context: TenantContext, kind: str, item_id: str) -> dict[str, Any] | None:
        self._table(kind)
        office_table = _SQL_TABLES[kind]
        row = self.session.execute(select(office_table).where(
            office_table.c.tenant_id == context.tenant_id,
            office_table.c.workspace_id == context.workspace_id,
            office_table.c.id == item_id,
        )).mappings().first()
        if row is None:
            return None
        record = dict(row)
        if kind == "desks":
            self._redact_invisible_agents(context, [record])
        return record

    def _redact_invisible_agents(self, context: TenantContext, records: list[dict[str, Any]]) -> None:
        if not any(record.get("agent_id") for record in records):
            return
        # Existing agent_profiles RLS hides private agents from other users.
        # Never disclose the id merely because a desk row is readable.
        visible = {
            row[0] for row in self.session.execute(text("""
                select id from agent_profiles
                where tenant_id = :tenant and workspace_id = :workspace
            """), self._params(context))
        }
        for record in records:
            if record.get("agent_id") not in visible:
                record["agent_id"] = None

    def create(self, context: TenantContext, kind: str, item_id: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        self._table(kind)
        office_table = _SQL_TABLES[kind]
        expected = set(_COLUMNS[kind])
        if set(fields) != expected:
            raise SpaceStoreError("invalid", "Unexpected office record fields")
        if kind == "desks":
            # Lock the room before counting desks. Concurrent creations serialize
            # here, so capacity cannot be exceeded by two owner requests.
            room = self.session.execute(text("""
                select kind, capacity from office_rooms
                where tenant_id = :tenant and workspace_id = :workspace and id = :room
                for update
            """), self._params(context, room=fields["room_id"])).mappings().first()
            if room is None:
                raise SpaceStoreError("not_found", "Office room not found")
            if room["kind"] not in ("office", "workshop", "warehouse"):
                raise SpaceStoreError("restricted", "Only work areas may have permanent desks")
            count = self.session.execute(text("""
                select count(*) from office_desks
                where tenant_id = :tenant and workspace_id = :workspace and room_id = :room
            """), self._params(context, room=fields["room_id"])).scalar_one()
            if count >= room["capacity"]:
                raise SpaceStoreError("restricted", "Room workstation capacity is full")
            if fields["agent_id"] is not None:
                raise SpaceStoreError("invalid", "Assign an existing agent in a separate step")
        values = {"id": item_id, "tenant_id": context.tenant_id, "workspace_id": context.workspace_id,
                  "created_by": context.user_id, **dict(fields)}
        try:
            result = self.session.execute(insert(office_table).values(**values).returning(office_table)).mappings().one()
            return dict(result)
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "Room, floor, desk or building already exists, or parent is unavailable") from exc

    def create_with_key(
        self, context: TenantContext, kind: str, item_id: str,
        fields: Mapping[str, Any], key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        """Claim a key and create in the same transaction; False means a replay.

        PostgreSQL's unique index waits for an in-flight claim before deciding the
        conflict, so two concurrent requests cannot create twice with one key.
        """
        if key is None:
            return self.create(context, kind, item_id, fields), True
        payload = {"kind": kind, "id": item_id, "fields": dict(fields)}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        params = self._params(context, key=key, digest=digest, kind=kind, id=item_id)
        claimed = self.session.execute(text("""
            insert into office_create_keys
                (tenant_id, workspace_id, key, request_hash, resource_kind, resource_id)
            values (:tenant, :workspace, :key, :digest, :kind, :id)
            on conflict (tenant_id, workspace_id, key) do nothing
            returning key
        """), params).first()
        if claimed is not None:
            return self.create(context, kind, item_id, fields), True
        existing = self.session.execute(text("""
            select request_hash, resource_kind, resource_id from office_create_keys
            where tenant_id = :tenant and workspace_id = :workspace and key = :key
        """), params).mappings().one()
        if (existing["request_hash"], existing["resource_kind"], existing["resource_id"]) != (digest, kind, item_id):
            raise SpaceStoreError("conflict", "Idempotency key was used for a different office create request")
        row = self.get(context, kind, item_id)
        if row is None:
            raise SpaceStoreError("conflict", "Previously created office record is no longer available")
        return row, False

    def assignment(self, context: TenantContext, desk_id: str, agent_id: str | None, revision: int) -> dict[str, Any]:
        # Agent visibility is user-scoped; private profiles must never be assigned by unauthorized users.
        if agent_id is not None:
            result = self.session.execute(text("""
                select 1 from agent_profiles
                where tenant_id = :tenant and workspace_id = :workspace and id = :agent
            """), self._params(context, agent=agent_id)).first()
            if result is None:
                raise SpaceStoreError("not_found", "The agent is not visible in this workspace")
        try:
            row = self.session.execute(text("""
                update office_desks set agent_id = :agent, revision = revision + 1, updated_at = now()
                where tenant_id = :tenant and workspace_id = :workspace
                  and id = :id and revision = :revision
                returning *
            """), self._params(context, agent=agent_id, id=desk_id, revision=revision)).mappings().first()
            if row is None:
                self._raise_revision(context, "desks", desk_id)
            return dict(row)
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "This agent already occupies another desk") from exc

    def delete_desk(self, context: TenantContext, desk_id: str, revision: int) -> None:
        result = self.session.execute(text("""
            delete from office_desks where tenant_id = :tenant and workspace_id = :workspace
            and id = :id and revision = :revision returning id
        """), self._params(context, id=desk_id, revision=revision)).first()
        if result is None:
            self._raise_revision(context, "desks", desk_id)

    def _raise_revision(self, context: TenantContext, kind: str, item_id: str) -> None:
        if self.get(context, kind, item_id) is None:
            raise SpaceStoreError("not_found", "Office record not found in this workspace")
        raise SpaceStoreError("conflict", _STALE, reason=layout.STALE_REVISION)


    # ------------------------------------------------------------------ builder writes
    # Every mutation below returns the row(s) before and after, so the API can audit the
    # change with both values. All of them run in the caller's single transaction.

    def _scope(self, kind: str, context: TenantContext):
        t = _SQL_TABLES[kind]
        return (t.c.tenant_id == context.tenant_id, t.c.workspace_id == context.workspace_id)

    def _lock(self, context: TenantContext, kind: str, item_id: str) -> dict[str, Any] | None:
        t = _SQL_TABLES[kind]
        row = self.session.execute(
            select(t).where(*self._scope(kind, context), t.c.id == item_id).with_for_update()
        ).mappings().first()
        return dict(row) if row else None

    def _lock_checked(self, context: TenantContext, kind: str, item_id: str, revision: int) -> dict[str, Any]:
        row = self._lock(context, kind, item_id)
        if row is None:
            raise SpaceStoreError("not_found", "Office record not found in this workspace")
        if row["revision"] != revision:
            raise SpaceStoreError("conflict", _STALE, reason=layout.STALE_REVISION)
        return row

    def _apply(self, context: TenantContext, kind: str, item_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        t = _SQL_TABLES[kind]
        if not changes or not set(changes) <= set(_PATCHABLE[kind]) | {"scene_x", "scene_z", "scene_width", "scene_depth"}:
            raise SpaceStoreError("invalid", "Unexpected office record fields")
        stmt = (
            update(t).where(*self._scope(kind, context), t.c.id == item_id)
            .values(**changes, revision=t.c.revision + 1, updated_at=func.now()).returning(t)
        )
        return dict(self.session.execute(stmt).mappings().one())

    def _floor_rooms(self, context: TenantContext, floor_id: str, lock: bool = False) -> list[dict[str, Any]]:
        t = _SQL_TABLES["rooms"]
        query = select(t).where(*self._scope("rooms", context), t.c.floor_id == floor_id).order_by(t.c.id)
        if lock:
            query = query.with_for_update()
        return [dict(r) for r in self.session.execute(query).mappings().all()]

    def _room_desks(self, context: TenantContext, room_ids: list[str], lock: bool = False) -> list[dict[str, Any]]:
        if not room_ids:
            return []
        t = _SQL_TABLES["desks"]
        query = select(t).where(*self._scope("desks", context), t.c.room_id.in_(room_ids)).order_by(t.c.id)
        if lock:
            query = query.with_for_update()
        return [dict(r) for r in self.session.execute(query).mappings().all()]

    def patch(self, context: TenantContext, kind: str, item_id: str, revision: int,
              changes: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Revision checked partial update of a building, floor, room or desk."""
        before = self._lock_checked(context, kind, item_id, revision)
        if kind == "floors":
            self._check_floor_patch(context, before, changes)
        elif kind == "rooms":
            self._check_room_patch(context, before, changes)
        elif kind == "desks":
            self._check_desk_patch(context, before, changes)
        try:
            after = self._apply(context, kind, item_id, changes)
        except IntegrityError as exc:
            raise _conflict_from(exc, "That change conflicts with an existing office record") from exc
        return before, after

    def _check_floor_patch(self, context: TenantContext, floor: dict[str, Any], changes: Mapping[str, Any]) -> None:
        if "width_m" not in changes:
            return
        bounds = (float(changes["width_m"]), float(changes["depth_m"]))
        issues = [
            layout.LayoutIssue(
                layout.ROOM_OUT_OF_BOUNDS, f"rooms.{room['id']}",
                f"Room {room['name']} would fall outside a floor of {bounds[0]:g} by {bounds[1]:g} metres",
            )
            for room in self._floor_rooms(context, floor["id"], lock=True)
            if room["scene_x"] is not None and not layout.within_bounds(_rect(room), *bounds)
        ]
        if issues:
            raise _refuse(issues, "Move or resize the rooms that would be outside the new floor size first")

    def _check_room_patch(self, context: TenantContext, room: dict[str, Any], changes: Mapping[str, Any]) -> None:
        if "kind" not in changes and "capacity" not in changes:
            return
        desks = self.session.execute(text("""
            select count(*) from office_desks
            where tenant_id = :tenant and workspace_id = :workspace and room_id = :room
        """), self._params(context, room=room["id"])).scalar_one()
        kind = changes.get("kind", room["kind"])
        capacity = changes.get("capacity", room["capacity"])
        if desks and kind not in layout.DESK_ROOM_KINDS:
            raise SpaceStoreError(
                "conflict", f"This room has {desks} desk(s) and a {kind} room cannot have desks. Remove them first",
                reason="KIND_FORBIDS_DESKS",
            )
        if capacity < desks:
            raise SpaceStoreError(
                "conflict", f"This room has {desks} desk(s); capacity cannot be below that",
                reason="CAPACITY_BELOW_DESKS",
            )

    def _check_desk_patch(self, context: TenantContext, desk: dict[str, Any], changes: Mapping[str, Any]) -> None:
        if changes.get("pos_x") is None:
            return
        room = self._lock(context, "rooms", desk["room_id"])
        if room is None or room["scene_x"] is None:
            raise SpaceStoreError(
                "invalid", "Place the room on the floor before positioning desks in it",
                reason=layout.ROOM_NOT_PLACED,
            )
        if not layout.point_in_room(_rect(room), changes["pos_x"], changes["pos_z"]):
            raise SpaceStoreError(
                "invalid", f"The desk must be inside the room ({room['scene_width']:g} by {room['scene_depth']:g} metres)",
                reason=layout.DESK_OUT_OF_ROOM,
            )

    def update_room_geometry(self, context: TenantContext, room_id: str, revision: int,
                             x: float, z: float, width: float, depth: float) -> tuple[dict[str, Any], dict[str, Any]]:
        """One room placement: same rules and floor lock as the atomic layout save."""
        room = self.get(context, "rooms", room_id)
        if room is None:
            raise SpaceStoreError("not_found", "Room not found")
        floor = self._lock(context, "floors", room["floor_id"])
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found")
        before = self._lock_checked(context, "rooms", room_id, revision)
        rooms = self._floor_rooms(context, floor["id"])
        rects = [_rect(r) for r in rooms if r["scene_x"] is not None and r["id"] != room_id]
        new = layout.Rect(room_id, x, z, width, depth)
        issues = layout.validate_layout([*rects, new], {room_id}, _bounds(floor))
        issues += self._desk_issues(context, {room_id: new})
        if issues:
            raise _refuse(issues, "The room does not fit there")
        after = self._apply(context, "rooms", room_id,
                            {"scene_x": x, "scene_z": z, "scene_width": width, "scene_depth": depth})
        return before, after

    def _desk_issues(self, context: TenantContext, final_rooms: Mapping[str, layout.Rect],
                     pos_override: Mapping[str, tuple[float, float] | None] | None = None) -> list[layout.LayoutIssue]:
        """Desks of the given rooms whose (possibly new) position leaves the room."""
        issues = []
        for desk in self._room_desks(context, list(final_rooms)):
            pos = (pos_override or {}).get(desk["id"], (desk["pos_x"], desk["pos_z"]) if desk["pos_x"] is not None else None)
            if pos is None:
                continue
            if not layout.point_in_room(final_rooms[desk["room_id"]], *pos):
                issues.append(layout.LayoutIssue(
                    layout.DESK_OUT_OF_ROOM, f"desks.{desk['id']}",
                    f"Desk {desk['label']} would be outside its room",
                ))
        return issues

    def save_floor_layout(self, context: TenantContext, floor_id: str, rooms: list[Mapping[str, Any]],
                          desks: list[Mapping[str, Any]]) -> dict[str, Any]:
        """All or nothing: validate the final layout, then write every room and desk."""
        floor = self._lock(context, "floors", floor_id)
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        room_rows = {r["id"]: r for r in self._floor_rooms(context, floor_id, lock=True)}
        desk_rows = {d["id"]: d for d in self._room_desks(context, list(room_rows), lock=True)}
        issues: list[layout.LayoutIssue] = []
        stale: list[layout.LayoutIssue] = []
        for item in rooms:
            row = room_rows.get(item["id"])
            if row is None:
                issues.append(layout.LayoutIssue(layout.ROOM_NOT_ON_FLOOR, f"rooms.{item['id']}",
                                                 f"Room {item['id']} is not on this floor"))
            elif row["revision"] != item["expected_revision"]:
                stale.append(layout.LayoutIssue(layout.STALE_REVISION, f"rooms.{item['id']}",
                                                f"Room {row['name']} changed since you loaded it"))
        for item in desks:
            row = desk_rows.get(item["id"])
            if row is None:
                issues.append(layout.LayoutIssue(layout.DESK_NOT_ON_FLOOR, f"desks.{item['id']}",
                                                 f"Desk {item['id']} is not on this floor"))
            elif row["revision"] != item["expected_revision"]:
                stale.append(layout.LayoutIssue(layout.STALE_REVISION, f"desks.{item['id']}",
                                                f"Desk {row['label']} changed since you loaded it"))
        if issues:
            raise _refuse(issues, "The layout names records that are not on this floor")
        if stale:
            raise SpaceStoreError(
                "conflict", _STALE, reason=layout.STALE_REVISION,
                issues=tuple((i.location, i.code, i.message) for i in stale),
            )
        submitted = {item["id"]: item for item in rooms}
        final: dict[str, layout.Rect] = {}
        for rid, row in room_rows.items():
            if rid in submitted:
                it = submitted[rid]
                final[rid] = layout.Rect(rid, it["x"], it["z"], it["width"], it["depth"])
            elif row["scene_x"] is not None:
                final[rid] = _rect(row)
        issues = layout.validate_layout(list(final.values()), set(submitted), _bounds(floor))
        desk_pos = {d["id"]: (d["pos_x"], d["pos_z"]) for d in desks}
        for item in desks:
            room = final.get(desk_rows[item["id"]]["room_id"])
            if room is None:
                issues.append(layout.LayoutIssue(layout.ROOM_NOT_PLACED, f"desks.{item['id']}",
                                                 "Place the room before positioning desks in it"))
        placed_for_desks = {
            rid: rect for rid, rect in final.items()
            if rid in submitted or any(desk_rows[d["id"]]["room_id"] == rid for d in desks)
        }
        issues += self._desk_issues(context, placed_for_desks, desk_pos)
        if issues:
            raise _refuse(issues, f"The layout cannot be saved: {len(issues)} problem(s)")
        before_rooms, after_rooms, before_desks, after_desks = [], [], [], []
        for rid, it in submitted.items():
            changes: dict[str, Any] = {"scene_x": it["x"], "scene_z": it["z"],
                                       "scene_width": it["width"], "scene_depth": it["depth"]}
            if it.get("rotation") is not None:
                changes["rotation"] = it["rotation"]
            if it.get("door_side") is not None:
                changes["door_side"] = it["door_side"]
            before_rooms.append(room_rows[rid])
            after_rooms.append(self._apply(context, "rooms", rid, changes))
        for did, it in ((d["id"], d) for d in desks):
            before_desks.append(desk_rows[did])
            after_desks.append(self._apply(context, "desks", did, {"pos_x": it["pos_x"], "pos_z": it["pos_z"]}))
        return {"floor": floor, "before_rooms": before_rooms, "rooms": after_rooms,
                "before_desks": before_desks, "desks": after_desks}

    # ------------------------------------------------------------------ deletes
    def _blocked(self, message: str, reason: str, issues: list[tuple[str, str, str]]) -> SpaceStoreError:
        shown = issues[:20]
        if len(issues) > 20:
            shown.append(("", reason, f"and {len(issues) - 20} more"))
        return SpaceStoreError("conflict", message, reason=reason, issues=tuple(shown))

    def delete_building(self, context: TenantContext, building_id: str, revision: int) -> dict[str, Any]:
        before = self._lock_checked(context, "buildings", building_id, revision)
        t = _SQL_TABLES["floors"]
        floors = self.session.execute(
            select(t.c.id, t.c.name, t.c.level).where(*self._scope("floors", context), t.c.building_id == building_id)
            .order_by(t.c.level)
        ).mappings().all()
        if floors:
            raise self._blocked(
                f"This building still has {len(floors)} floor(s). Delete the floors first",
                "BUILDING_NOT_EMPTY",
                [(f"floors.{f['id']}", "FLOOR_PRESENT", f"Floor {f['name']} (level {f['level']})") for f in floors],
            )
        try:
            self.session.execute(delete(_SQL_TABLES["buildings"]).where(
                *self._scope("buildings", context), _SQL_TABLES["buildings"].c.id == building_id))
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "Delete the floors first", reason="BUILDING_NOT_EMPTY") from exc
        return before

    def _room_desk_counts(self, context: TenantContext, floor_id: str) -> list[tuple[dict[str, Any], int]]:
        rooms = self._floor_rooms(context, floor_id)
        counts: dict[str, int] = {}
        for desk in self._room_desks(context, [r["id"] for r in rooms]):
            counts[desk["room_id"]] = counts.get(desk["room_id"], 0) + 1
        return [(room, counts.get(room["id"], 0)) for room in rooms]

    def delete_floor(self, context: TenantContext, floor_id: str, revision: int) -> dict[str, Any]:
        before = self._lock_checked(context, "floors", floor_id, revision)
        rooms = self._room_desk_counts(context, floor_id)
        if rooms:
            raise self._blocked(
                f"This floor still has {len(rooms)} room(s). Delete the rooms first",
                "FLOOR_NOT_EMPTY",
                [(f"rooms.{r['id']}", "ROOM_PRESENT", f"Room {r['name']} ({n} desk(s))") for r, n in rooms],
            )
        try:
            self.session.execute(delete(_SQL_TABLES["floors"]).where(
                *self._scope("floors", context), _SQL_TABLES["floors"].c.id == floor_id))
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "Delete the rooms first", reason="FLOOR_NOT_EMPTY") from exc
        return before

    def delete_room(self, context: TenantContext, room_id: str, revision: int) -> dict[str, Any]:
        before = self._lock_checked(context, "rooms", room_id, revision)
        desks = self._room_desks(context, [room_id])
        if desks:
            raise self._blocked(
                f"This room still has {len(desks)} desk(s). Remove the desks first",
                "ROOM_NOT_EMPTY",
                [(f"desks.{d['id']}", "DESK_PRESENT", f"Desk {d['label']}") for d in desks],
            )
        try:
            self.session.execute(delete(_SQL_TABLES["rooms"]).where(
                *self._scope("rooms", context), _SQL_TABLES["rooms"].c.id == room_id))
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "Remove the desks before deleting the room", reason="ROOM_NOT_EMPTY") from exc
        return before

    # ------------------------------------------------------------------ copy and templates
    def _place(self, context: TenantContext, floor: dict[str, Any], width: float, depth: float,
               x: float | None, z: float | None, near: layout.Rect | None) -> tuple[float, float]:
        placed = [_rect(r) for r in self._floor_rooms(context, floor["id"]) if r["scene_x"] is not None]
        bounds = _bounds(floor)
        if x is not None and z is not None:
            candidate = layout.Rect("new", x, z, width, depth)
            issues = layout.validate_layout([*placed, candidate], {"new"}, bounds)
            if issues:
                raise _refuse(issues, "The room does not fit there")
            return (x, z)
        spot = layout.find_free_spot(placed, width, depth, bounds, near)
        if spot is None:
            raise SpaceStoreError("conflict", "There is no free space for another room of this size on that floor",
                                  reason=layout.NO_FREE_SPACE)
        return spot

    def _insert_room(self, context: TenantContext, values: dict[str, Any]) -> dict[str, Any]:
        t = _SQL_TABLES["rooms"]
        try:
            return dict(self.session.execute(
                insert(t).values(tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                 created_by=context.user_id, **values).returning(t)
            ).mappings().one())
        except IntegrityError as exc:
            raise _conflict_from(exc, "A room with that id already exists, or the floor is unavailable") from exc

    def _insert_desks(self, context: TenantContext, room_id: str,
                      desks: list[tuple[str, str, tuple[float, float] | None]]) -> list[dict[str, Any]]:
        t = _SQL_TABLES["desks"]
        out = []
        for desk_id, label, pos in desks:
            try:
                out.append(dict(self.session.execute(insert(t).values(
                    id=desk_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                    created_by=context.user_id, room_id=room_id, label=label, agent_id=None,
                    pos_x=pos[0] if pos else None, pos_z=pos[1] if pos else None,
                ).returning(t)).mappings().one()))
            except IntegrityError as exc:
                raise _conflict_from(exc, "A desk with that id already exists") from exc
        return out

    def copy_room(self, context: TenantContext, source_id: str, new_id: str, name: str | None,
                  floor_id: str | None, x: float | None, z: float | None,
                  copy_desks: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        source = self.get(context, "rooms", source_id)
        if source is None:
            raise SpaceStoreError("not_found", "Room not found in this workspace")
        floor = self._lock(context, "floors", floor_id or source["floor_id"])
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        values: dict[str, Any] = {"id": new_id, "floor_id": floor["id"],
                                  "name": name or f"{source['name']} copy",
                                  **{c: source[c] for c in _ROOM_COPY_COLUMNS}}
        if source["scene_x"] is not None:
            near = _rect(source) if floor["id"] == source["floor_id"] else None
            px, pz = self._place(context, floor, source["scene_width"], source["scene_depth"], x, z, near)
            values.update(scene_x=px, scene_z=pz, scene_width=source["scene_width"], scene_depth=source["scene_depth"])
        room = self._insert_room(context, values)
        desks: list[dict[str, Any]] = []
        if copy_desks:
            old = self._room_desks(context, [source_id])
            desks = self._insert_desks(context, new_id, [
                (f"{new_id[:72]}-d{i + 1}", d["label"], (d["pos_x"], d["pos_z"]) if d["pos_x"] is not None else None)
                for i, d in enumerate(old)
            ])
        return room, desks

    def create_room_from_template(self, context: TenantContext, template: layout.RoomTemplate, room_id: str,
                                  floor_id: str, name: str | None, x: float | None, z: float | None,
                                  with_desks: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        floor = self._lock(context, "floors", floor_id)
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        px, pz = self._place(context, floor, template.width, template.depth, x, z, None)
        room = self._insert_room(context, {
            "id": room_id, "floor_id": floor_id, "name": name or template.name, "kind": template.kind,
            "capacity": template.capacity, "door_side": template.door_side, "color": template.color,
            "name_ar": template.name_ar if name is None else None,
            "equipment": [{"kind": k, "quantity": q} for k, q in template.equipment],
            "scene_x": px, "scene_z": pz, "scene_width": template.width, "scene_depth": template.depth,
        })
        desks: list[dict[str, Any]] = []
        if with_desks and template.kind in layout.DESK_ROOM_KINDS:
            count = min(template.desks, template.capacity)
            spots = layout.desk_grid(template.width, template.depth, count)
            desks = self._insert_desks(context, room_id, [
                (f"{room_id[:72]}-d{i + 1}", f"Desk {i + 1}", spot) for i, spot in enumerate(spots)
            ])
        return room, desks

    # ------------------------------------------------------------------ team link
    def team_summary(self, context: TenantContext, room_id: str) -> dict[str, Any]:
        """Real counts from the team records for the room's linked department."""
        room = self.get(context, "rooms", room_id)
        if room is None:
            raise SpaceStoreError("not_found", "Room not found in this workspace")
        department = room["team_department"]
        summary: dict[str, Any] = {"room_id": room_id, "department": department,
                                   "member_profiles": None, "assistants": None}
        if department is not None:
            params = self._params(context, department=department)
            summary["member_profiles"] = self.session.execute(text("""
                select count(*) from team_member_profiles
                where tenant_id = :tenant and workspace_id = :workspace and department = :department
            """), params).scalar_one()
            summary["assistants"] = self.session.execute(text("""
                select count(*) from assistant_profiles
                where tenant_id = :tenant and workspace_id = :workspace and department = :department and enabled
            """), params).scalar_one()
        return summary
