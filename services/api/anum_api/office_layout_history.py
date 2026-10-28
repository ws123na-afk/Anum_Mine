"""Layout revision history of an office floor (docs/ANUM_MINE_LAYOUT_HISTORY.md).

Every committed change to a floor's layout (rooms, desks, positions, rotation, doors, equipment) writes one
append-only snapshot row in the SAME transaction as the change, so a restore is exact and is never rebuilt
from audit text. This module holds

* pure helpers (snapshot shape, canonical hash, diff, restore plan) that need no database, and
* ``LayoutHistoryMixin``, the store methods that write, list and restore revisions. The mixin is part of
  ``SqlOfficeSpaceStore`` and relies on its locking and row helpers.

A snapshot never contains agents: a restore changes the layout, not who sits where. A desk that a restore would
remove while an agent sits at it is listed, and the restore is refused until the person confirms that exact list.

Version 2 snapshots also hold each desk's seat fields (shift, place at the table, facing, seat kind, table group) and the
shift hours of the floor's building, so a restore brings shift seating back exactly (docs/ANUM_MINE_GREEN_CONCRETE.md).
Version 3 adds each desk's own working hours (``work_start``, ``work_end``). An older snapshot never recorded what came
later: restoring a version 1 revision compares and changes only the version 1 fields, and a version 2 revision leaves the
working hours as they are now.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import text

from . import office_layout as layout
from .office_space_errors import SpaceStoreError
from .schemas import TenantContext
from .settings import settings

SNAPSHOT_VERSION: Final = 3
ROOM_FIELDS: Final = (
    "id", "name", "kind", "capacity", "scene_x", "scene_z", "scene_width", "scene_depth", "rotation",
    "door_side", "description", "notes", "color", "name_ar", "equipment", "team_department",
)
DESK_FIELDS_V1: Final = ("id", "room_id", "label", "pos_x", "pos_z")
SEAT_FIELDS_V2: Final = ("shift", "seat_order", "facing", "seat_kind", "table_group")
HOUR_FIELDS: Final = ("work_start", "work_end")
SEAT_FIELDS: Final = (*SEAT_FIELDS_V2, *HOUR_FIELDS)
DESK_FIELDS_V2: Final = (*DESK_FIELDS_V1, *SEAT_FIELDS_V2)
DESK_FIELDS: Final = (*DESK_FIELDS_V1, *SEAT_FIELDS)
SHIFT_FIELDS: Final = ("shift_day_start", "shift_night_start")
_GEOMETRY: Final = ("scene_x", "scene_z", "scene_width", "scene_depth", "rotation", "door_side")
STALE_LAYOUT: Final = "STALE_LAYOUT"
UNASSIGN_NOT_CONFIRMED: Final = "UNASSIGN_NOT_CONFIRMED"
REVISION_NOT_FOUND: Final = "REVISION_NOT_FOUND"
PAYLOAD_CHANGED: Final = "PAYLOAD_HASH_MISMATCH"
RESTORE_MISMATCH: Final = "RESTORE_MISMATCH"
# Actions that change a floor's layout. Anything else (desk assignment, team link reads) never writes a revision.
LAYOUT_ACTIONS: Final = frozenset({
    "office.floor.create", "office.floor.update", "office.floor.layout", "office.floor.restore",
    "office.floor.create_from_template",
    "office.room.create", "office.room.update", "office.room.rename", "office.room.delete",
    "office.room.geometry", "office.room.copy",
    "office.desk.create", "office.desk.update", "office.desk.delete",
})


def _norm(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def build_snapshot(floor: Mapping[str, Any], rooms: list[Mapping[str, Any]], desks: list[Mapping[str, Any]],
                   building: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The stored shape: floor size, the building's shift hours, rooms and desks (with seat fields) sorted by id.

    No agents and no revision counters.
    """
    return {
        "v": SNAPSHOT_VERSION,
        "floor": {"width_m": _norm(floor.get("width_m")), "depth_m": _norm(floor.get("depth_m"))},
        "shifts": {f: (building or {}).get(f) for f in SHIFT_FIELDS},
        "rooms": sorted(({f: _norm(r.get(f)) for f in ROOM_FIELDS} for r in rooms), key=lambda r: r["id"]),
        "desks": sorted(({f: _norm(d.get(f)) for f in DESK_FIELDS} for d in desks), key=lambda d: d["id"]),
    }


def desk_fields_of(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    """The desk fields a snapshot recorded: version 1 never held the seat fields, version 2 never held the working hours."""
    v = snapshot.get("v", 1)
    return DESK_FIELDS if v >= 3 else DESK_FIELDS_V2 if v == 2 else DESK_FIELDS_V1


def seat_fields_of(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    """The seat fields a snapshot recorded (none in version 1)."""
    v = snapshot.get("v", 1)
    return SEAT_FIELDS if v >= 3 else SEAT_FIELDS_V2 if v == 2 else ()


def as_version_of(snapshot: Mapping[str, Any], target: Mapping[str, Any]) -> Mapping[str, Any]:
    """``snapshot`` reduced to what ``target`` recorded, so a newer floor compares with an older revision."""
    v = target.get("v", 1)
    if v >= SNAPSHOT_VERSION:
        return snapshot
    if v == 2:
        return {"v": 2, "floor": snapshot["floor"], "shifts": snapshot.get("shifts"), "rooms": snapshot["rooms"],
                "desks": [{f: d.get(f) for f in DESK_FIELDS_V2} for d in snapshot["desks"]]}
    return {"v": 1, "floor": snapshot["floor"], "rooms": snapshot["rooms"],
            "desks": [{f: d.get(f) for f in DESK_FIELDS_V1} for d in snapshot["desks"]]}


def snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    content: dict[str, Any] = {"floor": snapshot["floor"], "rooms": snapshot["rooms"], "desks": snapshot["desks"]}
    if "shifts" in snapshot:
        content["shifts"] = snapshot["shifts"]
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def restore_payload_hash(tenant_id: str, workspace_id: str, user_id: str, floor_id: str,
                         revision_no: int, target_hash: str) -> str:
    """What the person was shown: bound to who confirms, where, and the exact revision content."""
    raw = json.dumps(["office.floor.restore", tenant_id, workspace_id, user_id, floor_id, revision_no, target_hash],
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _by_id(items: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {i["id"]: i for i in items}


def _box(room: Mapping[str, Any]) -> dict[str, Any] | None:
    if room.get("scene_x") is None:
        return None
    return {"x": room["scene_x"], "z": room["scene_z"], "width": room["scene_width"], "depth": room["scene_depth"],
            "rotation": room.get("rotation") or 0}


def diff_snapshots(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """What going from ``old`` to ``new`` does. ``old`` is None for the first revision (everything counts as present)."""
    old_rooms = _by_id(old["rooms"]) if old else {}
    new_rooms = _by_id(new["rooms"])
    rooms: list[dict[str, Any]] = []
    counts = {"rooms_added": 0, "rooms_removed": 0, "rooms_moved": 0, "rooms_changed": 0}
    for rid in sorted(set(old_rooms) | set(new_rooms)):
        before, after = old_rooms.get(rid), new_rooms.get(rid)
        if old is None:
            status = "same"
        elif before is None:
            status = "added"
        elif after is None:
            status = "removed"
        elif any(before[f] != after[f] for f in _GEOMETRY):
            status = "moved"
        elif any(before[f] != after[f] for f in ROOM_FIELDS):
            status = "changed"
        else:
            status = "same"
        key = {"added": "rooms_added", "removed": "rooms_removed", "moved": "rooms_moved", "changed": "rooms_changed"}.get(status)
        if key:
            counts[key] += 1
        shown = after if after is not None else before
        if shown is None:  # unreachable: the id came from one of the two sides
            continue
        rooms.append({
            "id": rid, "name": shown["name"], "kind": shown["kind"], "color": shown["color"], "status": status,
            "box": _box(shown), "from_box": _box(before) if before is not None and after is not None else None,
            "from_name": before["name"] if before is not None and after is not None and before["name"] != after["name"] else None,
        })
    old_desks = _by_id(old["desks"]) if old else {}
    new_desks = _by_id(new["desks"])
    d_added = d_removed = d_moved = d_seats = 0
    seat_fields = tuple(f for f in seat_fields_of(old) if f in seat_fields_of(new)) if old is not None else ()
    if old is not None:
        for did in set(old_desks) | set(new_desks):
            before, after = old_desks.get(did), new_desks.get(did)
            if before is None:
                d_added += 1
            elif after is None:
                d_removed += 1
            elif any(before[f] != after[f] for f in DESK_FIELDS_V1):
                d_moved += 1
            elif any(before.get(f) != after.get(f) for f in seat_fields):
                d_seats += 1
    resized = bool(old) and old["floor"] != new["floor"]
    hours = bool(old) and "shifts" in old and "shifts" in new and old["shifts"] != new["shifts"]
    return {
        "rooms": rooms,
        "desks": [{"id": d["id"], "room_id": d["room_id"], "label": d["label"], "x": d["pos_x"], "z": d["pos_z"]}
                  for d in new["desks"]],
        "summary": {**counts, "desks_added": d_added, "desks_removed": d_removed, "desks_moved": d_moved,
                    "seats_changed": d_seats, "shift_hours_changed": hours,
                    "floor_resized": resized, "rooms": len(new["rooms"]), "desks": len(new["desks"])},
    }


def restore_plan(current: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    """The row changes that turn ``current`` into ``target``, as ids and values (pure).

    Only the fields ``target`` recorded are compared and changed: a version 1 revision leaves seat fields and shift hours alone,
    and a desk it re-creates comes back without seat fields (they were never recorded); a version 2 revision leaves the working
    hours alone in the same way.
    """
    cur_rooms, tgt_rooms = _by_id(current["rooms"]), _by_id(target["rooms"])
    cur_desks, tgt_desks = _by_id(current["desks"]), _by_id(target["desks"])
    fields = desk_fields_of(target)
    hours = target.get("shifts")
    return {
        **({"shift_hours": hours} if hours is not None and hours != current.get("shifts") else {}),
        "desk_deletes": sorted(set(cur_desks) - set(tgt_desks)),
        "room_deletes": sorted(set(cur_rooms) - set(tgt_rooms)),
        "room_inserts": [tgt_rooms[i] for i in sorted(set(tgt_rooms) - set(cur_rooms))],
        "desk_inserts": [tgt_desks[i] for i in sorted(set(tgt_desks) - set(cur_desks))],
        "room_updates": [(i, {f: tgt_rooms[i][f] for f in ROOM_FIELDS if f != "id" and tgt_rooms[i][f] != cur_rooms[i][f]})
                         for i in sorted(set(tgt_rooms) & set(cur_rooms))
                         if any(tgt_rooms[i][f] != cur_rooms[i][f] for f in ROOM_FIELDS)],
        "desk_updates": [(i, {f: tgt_desks[i][f] for f in fields if f not in ("id", "room_id") and tgt_desks[i][f] != cur_desks[i].get(f)})
                         for i in sorted(set(tgt_desks) & set(cur_desks))
                         if any(tgt_desks[i][f] != cur_desks[i].get(f) for f in fields)],
    }


def revision_view(row: Mapping[str, Any], head_no: int) -> dict[str, Any]:
    return {
        "revision_no": row["revision_no"], "action": row["action"], "actor": row["actor"],
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        "summary": row["summary"], "snapshot_hash": row["snapshot_hash"],
        "restored_from": row["restored_from"], "is_current": row["revision_no"] == head_no,
    }


class LayoutHistoryMixin:
    """Store methods for the revision table. Mixed into ``SqlOfficeSpaceStore``."""

    # Provided by SqlOfficeSpaceStore.
    session: Any

    # ------------------------------------------------------------------ writing
    def _read_head(self, context: TenantContext, floor_id: str) -> dict[str, Any] | None:
        row = self.session.execute(text("""
            select revision_no, action, actor, created_at, snapshot, snapshot_hash, summary, restored_from, restored_over
            from office_floor_revisions
            where tenant_id = :tenant and workspace_id = :workspace and floor_id = :floor
            order by revision_no desc limit 1
        """), self._params(context, floor=floor_id)).mappings().first()  # type: ignore[attr-defined]
        return dict(row) if row else None

    def _current_snapshot(self, context: TenantContext, floor: Mapping[str, Any]) -> dict[str, Any]:
        rooms = self._floor_rooms(context, floor["id"])  # type: ignore[attr-defined]
        desks = self._room_desks(context, [r["id"] for r in rooms])  # type: ignore[attr-defined]
        building = self.get(context, "buildings", floor["building_id"])  # type: ignore[attr-defined]
        return build_snapshot(floor, rooms, desks, building)

    def _write_revision(self, context: TenantContext, floor_id: str, snapshot: dict[str, Any], action: str,
                        previous: Mapping[str, Any] | None, *, restored_from: int | None = None,
                        restored_over: int | None = None, baseline: bool = False) -> int:
        number = (previous["revision_no"] if previous else 0) + 1
        summary = diff_snapshots(previous["snapshot"] if previous else None, snapshot)["summary"]
        if baseline:
            summary["baseline"] = True
        self.session.execute(text("""
            insert into office_floor_revisions
                (tenant_id, workspace_id, floor_id, revision_no, action, actor, snapshot, snapshot_hash, summary,
                 restored_from, restored_over)
            values (:tenant, :workspace, :floor, :no, :action, :actor, cast(:snapshot as jsonb), :hash,
                    cast(:summary as jsonb), :rfrom, :rover)
        """), self._params(  # type: ignore[attr-defined]
            context, floor=floor_id, no=number, action=action[:80], actor=context.user_id,
            snapshot=json.dumps(snapshot), hash=snapshot_hash(snapshot), summary=json.dumps(summary),
            rfrom=restored_from, rover=restored_over))
        # Oldest first out: the cap bounds the table per floor. Only rows older than the cap window go.
        cap = settings.office_layout_revision_cap
        self.session.execute(text("""
            delete from office_floor_revisions
            where tenant_id = :tenant and workspace_id = :workspace and floor_id = :floor and revision_no <= :cut
        """), self._params(context, floor=floor_id, cut=number - cap))  # type: ignore[attr-defined]
        return number

    def begin_floor_change(self, context: TenantContext, floor_id: str) -> dict[str, Any] | None:
        """Lock the floor first (one lock order for every writer), then keep the layout as it is now.

        A floor that has no history yet gets a ``baseline`` revision of its state BEFORE this change, so the first
        change after the history feature shipped can be undone too. Returns the locked floor row, or None if gone.
        """
        floor = self._lock(context, "floors", floor_id)  # type: ignore[attr-defined]
        if floor is None:
            return None
        if self._read_head(context, floor_id) is None:
            self._write_revision(context, floor_id, self._current_snapshot(context, floor), "baseline", None, baseline=True)
        return floor

    def floor_id_of(self, context: TenantContext, kind: str, item_id: str) -> str | None:
        """The floor a room or desk belongs to (parents never change), without locking anything."""
        if kind == "floors":
            return item_id
        if kind == "rooms":
            sql = "select floor_id from office_rooms where tenant_id = :tenant and workspace_id = :workspace and id = :id"
        elif kind == "desks":
            sql = ("select r.floor_id from office_desks d join office_rooms r on r.tenant_id = d.tenant_id "
                   "and r.workspace_id = d.workspace_id and r.id = d.room_id "
                   "where d.tenant_id = :tenant and d.workspace_id = :workspace and d.id = :id")
        else:
            return None
        return self.session.execute(text(sql), self._params(context, id=item_id)).scalar()  # type: ignore[attr-defined]

    def record_floor_revision(self, context: TenantContext, floor_id: str, action: str) -> int | None:
        """Snapshot the floor as it is now, unless nothing changed since the head. Called once per transaction."""
        floor = self._lock(context, "floors", floor_id)  # type: ignore[attr-defined]
        if floor is None:
            return None  # the floor itself was deleted: its history went with it
        head = self._read_head(context, floor_id)
        snapshot = self._current_snapshot(context, floor)
        if head is not None and head["snapshot_hash"] == snapshot_hash(snapshot):
            return None
        return self._write_revision(context, floor_id, snapshot, action, head)

    # ------------------------------------------------------------------ reading
    def revision_head(self, context: TenantContext, floor_id: str) -> int:
        head = self._read_head(context, floor_id)
        return head["revision_no"] if head else 0

    def list_revisions(self, context: TenantContext, floor_id: str, before: int | None, limit: int) -> dict[str, Any]:
        if self.get(context, "floors", floor_id) is None:  # type: ignore[attr-defined]
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        head_no = self.revision_head(context, floor_id)
        rows = self.session.execute(text("""
            select revision_no, action, actor, created_at, summary, snapshot_hash, restored_from
            from office_floor_revisions
            where tenant_id = :tenant and workspace_id = :workspace and floor_id = :floor
              and (cast(:before as integer) is null or revision_no < cast(:before as integer))
            order by revision_no desc limit :limit
        """), self._params(context, floor=floor_id, before=before, limit=limit + 1)).mappings().all()  # type: ignore[attr-defined]
        items = [revision_view(dict(r), head_no) for r in rows[:limit]]
        return {"items": items, "head_revision": head_no, "cap": settings.office_layout_revision_cap,
                "next_before": items[-1]["revision_no"] if len(rows) > limit and items else None}

    def _revision_row(self, context: TenantContext, floor_id: str, number: int) -> dict[str, Any]:
        row = self.session.execute(text("""
            select revision_no, action, actor, created_at, snapshot, snapshot_hash, summary, restored_from, restored_over
            from office_floor_revisions
            where tenant_id = :tenant and workspace_id = :workspace and floor_id = :floor and revision_no = :no
        """), self._params(context, floor=floor_id, no=number)).mappings().first()  # type: ignore[attr-defined]
        if row is None:
            raise SpaceStoreError("not_found", "That revision does not exist or was removed to keep the history short",
                                  reason=REVISION_NOT_FOUND)
        return dict(row)

    def _unassign_list(self, context: TenantContext, current: Mapping[str, Any], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Desks a restore removes while an agent sits at them (all of them, including private agents)."""
        gone_rooms = set(plan["room_deletes"])
        gone = set(plan["desk_deletes"])
        room_names = {r["id"]: r["name"] for r in current["rooms"]}
        wanted = {d["id"]: d for d in current["desks"] if d["id"] in gone or d["room_id"] in gone_rooms}
        if not wanted:
            return []
        rows = self.session.execute(text("""
            select id, agent_id from office_desks
            where tenant_id = :tenant and workspace_id = :workspace and agent_id is not null and id = any(:ids)
        """), self._params(context, ids=list(wanted))).mappings().all()  # type: ignore[attr-defined]
        seated = {r["id"]: r["agent_id"] for r in rows}
        shown = [{"id": did, "agent_id": seated[did]} for did in seated]
        self._redact_invisible_agents(context, shown)  # type: ignore[attr-defined]
        return [
            {"desk_id": s["id"], "label": wanted[s["id"]]["label"], "room": room_names.get(wanted[s["id"]]["room_id"], ""),
             "agent_id": s["agent_id"], "agent_hidden": s["agent_id"] is None}
            for s in sorted(shown, key=lambda s: s["id"])
        ]

    def revision_detail(self, context: TenantContext, floor_id: str, number: int, against: str,
                        user_id: str) -> dict[str, Any]:
        """One revision with its diff: ``against`` is ``current`` (what a restore would do) or ``previous``."""
        floor = self.get(context, "floors", floor_id)  # type: ignore[attr-defined]
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        row = self._revision_row(context, floor_id, number)
        head = self._read_head(context, floor_id)
        head_no = head["revision_no"] if head else 0
        current = self._current_snapshot(context, floor)
        out: dict[str, Any] = {
            "revision": revision_view(row, head_no), "head_revision": head_no, "against": against,
            "current_hash": snapshot_hash(current),
            "payload_hash": restore_payload_hash(context.tenant_id, context.workspace_id, user_id, floor_id, number, row["snapshot_hash"]),
            "matches_current": row["snapshot_hash"] == snapshot_hash(as_version_of(current, row["snapshot"])),
            "floor": row["snapshot"]["floor"],
        }
        if against == "previous":
            previous = self.session.execute(text("""
                select snapshot from office_floor_revisions
                where tenant_id = :tenant and workspace_id = :workspace and floor_id = :floor and revision_no < :no
                order by revision_no desc limit 1
            """), self._params(context, floor=floor_id, no=number)).mappings().first()  # type: ignore[attr-defined]
            out["diff"] = diff_snapshots(previous["snapshot"] if previous else None, row["snapshot"])
            out["unassign"] = []
        else:
            out["diff"] = diff_snapshots(current, row["snapshot"])
            plan = restore_plan(current, row["snapshot"])
            out["unassign"] = self._unassign_list(context, current, plan)
        return out

    # ------------------------------------------------------------------ restore
    def restore_floor_revision(self, context: TenantContext, floor_id: str, number: int, *, payload_hash: str,
                               expected_head: int, confirm_unassign: list[str]) -> dict[str, Any]:
        """Make the floor look exactly as revision ``number`` did, as a NEW revision. History is never rewritten."""
        floor = self.begin_floor_change(context, floor_id)
        if floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        head = self._read_head(context, floor_id)
        if head is None:  # begin_floor_change always leaves a head
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        # A retry of a restore that already went through answers with its result instead of failing as stale.
        if head["restored_from"] == number and head["restored_over"] == expected_head:
            return {"restored": True, "replayed": True, "noop": False, "revision_no": head["revision_no"],
                    "restored_from": number, "unassigned": [], "before": None,
                    "after": head["snapshot"], "summary": head["summary"]}
        if head["revision_no"] != expected_head:
            raise SpaceStoreError("conflict", "The floor changed since you opened this. Reload the history and review it again",
                                  reason=STALE_LAYOUT)
        target = self._revision_row(context, floor_id, number)
        expected_payload = restore_payload_hash(context.tenant_id, context.workspace_id, context.user_id, floor_id,
                                                number, target["snapshot_hash"])
        if not hmac.compare_digest(expected_payload, payload_hash):
            raise SpaceStoreError("conflict", "What you confirmed is not what this revision holds. Review it again",
                                  reason=PAYLOAD_CHANGED)
        # Lock everything we may touch, in the builder's order (floor, rooms, desks).
        self._floor_rooms(context, floor_id, lock=True)  # type: ignore[attr-defined]
        current = self._current_snapshot(context, floor)
        self._room_desks(context, [r["id"] for r in current["rooms"]], lock=True)  # type: ignore[attr-defined]
        snap = target["snapshot"]
        if snapshot_hash(as_version_of(current, snap)) == target["snapshot_hash"]:
            return {"restored": False, "replayed": False, "noop": True, "revision_no": head["revision_no"],
                    "restored_from": None, "unassigned": [], "before": current, "after": current,
                    "summary": head["summary"]}
        plan = restore_plan(current, snap)
        unassign = self._unassign_list(context, current, plan)
        if {u["desk_id"] for u in unassign} != set(confirm_unassign):
            raise SpaceStoreError(
                "conflict", f"This restore removes {len(unassign)} desk(s) with an agent. Confirm the list to continue"
                if unassign else "The list of desks to unassign changed. Review it again",
                reason=UNASSIGN_NOT_CONFIRMED,
                issues=tuple((f"desks.{u['desk_id']}", UNASSIGN_NOT_CONFIRMED, f"Desk {u['label']} in {u['room']}") for u in unassign[:50]),
            )
        self._check_restorable(snap)
        self._apply_plan(context, floor, snap, plan)
        after_floor = self._lock(context, "floors", floor_id)
        if after_floor is None:
            raise SpaceStoreError("not_found", "Floor not found in this workspace")
        after = self._current_snapshot(context, after_floor)
        if snapshot_hash(as_version_of(after, snap)) != target["snapshot_hash"]:
            raise SpaceStoreError("conflict", "The restore did not reproduce that revision, so nothing was changed",
                                  reason=RESTORE_MISMATCH)
        no = self._write_revision(context, floor_id, after, "office.floor.restore", head,
                                  restored_from=number, restored_over=head["revision_no"])
        return {"restored": True, "replayed": False, "noop": False, "revision_no": no, "restored_from": number,
                "unassigned": unassign, "before": current, "after": after,
                "summary": diff_snapshots(current, after)["summary"]}

    def _check_restorable(self, snap: Mapping[str, Any]) -> None:
        bounds = None
        if snap["floor"]["width_m"] is not None:
            bounds = (float(snap["floor"]["width_m"]), float(snap["floor"]["depth_m"]))
        rects = {r["id"]: layout.Rect(r["id"], r["scene_x"], r["scene_z"], r["scene_width"], r["scene_depth"])
                 for r in snap["rooms"] if r["scene_x"] is not None}
        issues = layout.validate_layout(list(rects.values()), set(rects), bounds)
        for d in snap["desks"]:
            room = rects.get(d["room_id"])
            if d["pos_x"] is not None and (room is None or not layout.point_in_room(room, d["pos_x"], d["pos_z"])):
                issues.append(layout.LayoutIssue(layout.DESK_OUT_OF_ROOM, f"desks.{d['id']}", f"Desk {d['label']} would be outside its room"))
        if issues:
            raise SpaceStoreError("invalid", "That revision no longer fits the floor", reason=issues[0].code,
                                  issues=tuple((i.location, i.code, i.message) for i in issues[:50]))

    def _apply_plan(self, context: TenantContext, floor: Mapping[str, Any], snap: Mapping[str, Any],
                    plan: Mapping[str, Any]) -> None:
        from sqlalchemy import delete

        from .office_space_store import _SQL_TABLES

        floor_id = floor["id"]
        desks_t, rooms_t = _SQL_TABLES["desks"], _SQL_TABLES["rooms"]
        gone_rooms = list(plan["room_deletes"])
        gone_desks = list(plan["desk_deletes"])
        if gone_rooms:
            # Desks of a removed room go with it (their ids are not in the target either).
            gone_desks += [d["id"] for d in self._room_desks(context, gone_rooms)]  # type: ignore[attr-defined]
        if gone_desks:
            self.session.execute(delete(desks_t).where(*self._scope("desks", context), desks_t.c.id.in_(sorted(set(gone_desks)))))  # type: ignore[attr-defined]
        if gone_rooms:
            self.session.execute(delete(rooms_t).where(*self._scope("rooms", context), rooms_t.c.id.in_(gone_rooms)))  # type: ignore[attr-defined]
        # Names and labels are unique per floor / room: park the ones that change under a unique placeholder first,
        # so two records can swap names inside one restore.
        for rid, changes in plan["room_updates"]:
            if "name" in changes:
                self._apply(context, "rooms", rid, {"name": f"~{rid}"[:100]})  # type: ignore[attr-defined]
        for did, changes in plan["desk_updates"]:
            if "label" in changes:
                self._apply(context, "desks", did, {"label": f"~{did}"[:100]})  # type: ignore[attr-defined]
        for room in plan["room_inserts"]:
            self._insert_room(context, {**room, "floor_id": floor_id})  # type: ignore[attr-defined]
        for desk in plan["desk_inserts"]:
            seat = {f: desk[f] for f in SEAT_FIELDS if desk.get(f) is not None}
            self._insert_desks(context, desk["room_id"], [(desk["id"], desk["label"],  # type: ignore[attr-defined]
                                                           (desk["pos_x"], desk["pos_z"]) if desk["pos_x"] is not None else None, seat)])
        for rid, changes in plan["room_updates"]:
            if changes:
                self._apply(context, "rooms", rid, changes)  # type: ignore[attr-defined]
        for did, changes in plan["desk_updates"]:
            if changes:
                self._apply(context, "desks", did, changes)  # type: ignore[attr-defined]
        if plan.get("shift_hours") is not None and floor.get("building_id"):
            # The hours live on the building (one pair for all its floors). Locked after the floor, as every restore does.
            self._lock(context, "buildings", floor["building_id"])  # type: ignore[attr-defined]
            self._apply(context, "buildings", floor["building_id"], dict(plan["shift_hours"]))  # type: ignore[attr-defined]
        width, depth = snap["floor"]["width_m"], snap["floor"]["depth_m"]
        if (floor.get("width_m"), floor.get("depth_m")) != (width, depth) and width is not None:
            self._apply(context, "floors", floor_id, {"width_m": width, "depth_m": depth})  # type: ignore[attr-defined]
