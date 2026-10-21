"""Workspace-scoped persistent office configuration repository.

Caller must supply a Session with tenant, workspace and user RLS context already set.
Every query repeats the scope predicate defensively. Updates use optimistic revisions.
No agent activity or private agent profile data are fabricated in this module.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .schemas import TenantContext


class SpaceStoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_TABLES = {
    "buildings": "office_buildings",
    "floors": "office_floors",
    "rooms": "office_rooms",
    "desks": "office_desks",
}
_PARENT = {"floors": ("building_id", "buildings"), "rooms": ("floor_id", "floors"), "desks": ("room_id", "rooms")}
_COLUMNS = {
    "buildings": ("name", "timezone", "latitude", "longitude"),
    "floors": ("building_id", "level", "name"),
    "rooms": ("floor_id", "name", "kind", "capacity"),
    "desks": ("room_id", "label", "agent_id"),
}


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
        table = self._table(kind)
        parent = _PARENT.get(kind)
        parent_condition = f"and {parent[0]} = :parent" if parent and parent_id is not None else ""
        cursor_condition = "and id > :cursor" if cursor else ""
        params = self._params(context, parent=parent_id, cursor=cursor, page_size=limit + 1)
        records = [dict(row) for row in self.session.execute(text(f"""
            select * from {table}
            where tenant_id = :tenant and workspace_id = :workspace
              {parent_condition} {cursor_condition}
            order by id limit :page_size
        """), params).mappings().all()]
        has_next = len(records) > limit
        records = records[:limit]
        if kind == "desks":
            self._redact_invisible_agents(context, records)
        return {"items": records, "next_cursor": records[-1]["id"] if has_next else None}

    def get(self, context: TenantContext, kind: str, item_id: str) -> dict[str, Any] | None:
        table = self._table(kind)
        row = self.session.execute(text(f"""
            select * from {table}
            where tenant_id = :tenant and workspace_id = :workspace and id = :id
        """), self._params(context, id=item_id)).mappings().first()
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
        table = self._table(kind)
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
        columns = ", ".join(_COLUMNS[kind])
        placeholders = ", ".join(f":{key}" for key in _COLUMNS[kind])
        params = self._params(context, id=item_id, actor=context.user_id, **dict(fields))
        try:
            result = self.session.execute(text(f"""
                insert into {table}
                  (id, tenant_id, workspace_id, created_by, {columns})
                values (:id, :tenant, :workspace, :actor, {placeholders})
                returning *
            """), params).mappings().one()
            return dict(result)
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "Room, floor, desk or building already exists, or parent is unavailable") from exc

    def rename_room(self, context: TenantContext, room_id: str, name: str, revision: int) -> dict[str, Any]:
        try:
            row = self.session.execute(text("""
                update office_rooms set name = :name, revision = revision + 1, updated_at = now()
                where tenant_id = :tenant and workspace_id = :workspace
                  and id = :id and revision = :revision
                returning *
            """), self._params(context, name=name, id=room_id, revision=revision)).mappings().first()
            if row is None:
                self._raise_revision(context, "rooms", room_id)
            return dict(row)
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "A room already has that name on this floor") from exc

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

    def delete_room(self, context: TenantContext, room_id: str, revision: int) -> None:
        # Restrictive foreign key rejects deletion when workstations still exist.
        try:
            result = self.session.execute(text("""
                delete from office_rooms where tenant_id = :tenant and workspace_id = :workspace
                and id = :id and revision = :revision returning id
            """), self._params(context, id=room_id, revision=revision)).first()
            if result is None:
                self._raise_revision(context, "rooms", room_id)
        except IntegrityError as exc:
            raise SpaceStoreError("conflict", "Remove the desks before deleting the room") from exc

    def _raise_revision(self, context: TenantContext, kind: str, item_id: str) -> None:
        if self.get(context, kind, item_id) is None:
            raise SpaceStoreError("not_found", "Office record not found in this workspace")
        raise SpaceStoreError("conflict", "Office layout changed. Reload before retrying")
