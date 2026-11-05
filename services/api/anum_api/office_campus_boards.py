"""ANUM MINE campus boards: the billboard facts of every other workspace on the campus.

``GET /api/v1/office/campus/boards`` answers, for each workspace the signed-in person actively belongs to
(other than the open one, whose live reads already cover it) and may read (``task:read``), what its building's
billboard and sign show: the open work counts, a short rooms and departments summary and the owner's sign
(style, brand colour, logo hash) of each of its registry buildings. See docs/ANUM_MINE_HQ_BILLBOARDS.md.

Isolation: the memberships come from the narrow membership directory role (as ``GET /office/campus``); every
workspace is then read in its OWN tenant and workspace RLS context with the application role, as the caller,
with the caller's role in that workspace. One short session per workspace, never a cross-tenant or
cross-workspace query, no BYPASSRLS, no SECURITY DEFINER, no role widening. A workspace without ``task:read``
is not listed; one that fails to read is listed as ``accessible: false`` with no numbers (nothing is guessed).

Bounded: at most ``MAX_PAGE`` workspaces a page (``limit``, ``cursor``), ``MAX_BOARD_BUILDINGS`` registry
buildings and ``MAX_BOARD_ROOMS`` rooms a workspace. Each workspace's answer is kept for ``CACHE_SECONDS``
per (tenant, workspace, user, role); the memberships are read fresh on every call, so a removed membership
or a changed role takes effect at once.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from datetime import datetime
from time import monotonic
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_flow import derive_flow, load_flow_evidence, thresholds_from_settings
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, utc_now
from .settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office", tags=["office campus"])

BOARDS_VERSION = 1
MAX_PAGE = 16
MAX_BOARD_BUILDINGS = 16
MAX_BOARD_ROOMS = 60
MAX_DEPARTMENTS = 24
CACHE_SECONDS = 15.0
CACHE_ENTRIES = 512

_cache: OrderedDict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(key: tuple[str, str, str, str]) -> dict[str, Any] | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None or monotonic() - hit[0] > CACHE_SECONDS:
            return None
        return hit[1]


def _store(key: tuple[str, str, str, str], board: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = (monotonic(), board)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)


def work_summary(items: list[dict[str, Any]], building_ids: list[str], *, source: str, truncated: bool) -> dict[str, Any]:
    """Open work of a workspace from its flow items: task items a record placed in a room, not done.

    Pure. The same rule as the open workspace's billboard (``workOf`` in the web): ``waiting`` is an item with a
    pending approval, ``blocked`` an item at the blocked stage. Per listed building and for the whole workspace.
    """
    if source != "connected":
        return {"source": source, "open": None, "waiting": None, "blocked": None, "truncated": False, "by_building": {}}
    zero = {"open": 0, "waiting": 0, "blocked": 0}
    total = dict(zero)
    by_building: dict[str, dict[str, int]] = {b: dict(zero) for b in building_ids}
    for item in items:
        location = item.get("location")
        if item.get("kind") != "task" or item.get("stage") == "done" or not location:
            continue
        rows = [total]
        target = by_building.get(location.get("building_id") or "")
        if target is not None:
            rows.append(target)
        for row in rows:
            row["open"] += 1
            if item.get("waiting"):
                row["waiting"] += 1
            if item.get("stage") == "blocked":
                row["blocked"] += 1
    return {"source": source, **total, "truncated": truncated, "by_building": by_building}


def _sign(row: Any) -> dict[str, Any] | None:
    if not (row.sign_style or row.brand_color or row.logo_sha256):
        return None
    return {"style": row.sign_style, "color": row.brand_color, "logo_sha256": row.logo_sha256}


def read_board(context: TenantContext, now: datetime) -> dict[str, Any]:
    """Blocking: one short session in ``context``'s own tenant and workspace."""
    p = {"t": context.tenant_id, "w": context.workspace_id}
    th = thresholds_from_settings(settings)
    with workspace_session(context) as session:
        building_rows = session.execute(text(
            "select id, name, name_ar, sign_style, brand_color, logo_sha256 from office_buildings "
            "where tenant_id = :t and workspace_id = :w order by id limit :n"), {**p, "n": MAX_BOARD_BUILDINGS + 1}).all()
        counts = session.execute(text(
            "select (select count(*) from office_buildings where tenant_id = :t and workspace_id = :w) as buildings, "
            "(select count(*) from office_rooms where tenant_id = :t and workspace_id = :w) as rooms"), p).one()
        floors = {r.building_id: r.n for r in session.execute(text(
            "select building_id, count(*) as n from office_floors where tenant_id = :t and workspace_id = :w "
            "group by building_id"), p)}
        rooms_per = {r.building_id: r.n for r in session.execute(text(
            "select f.building_id, count(*) as n from office_rooms r "
            "join office_floors f on f.tenant_id = r.tenant_id and f.workspace_id = r.workspace_id and f.id = r.floor_id "
            "where r.tenant_id = :t and r.workspace_id = :w group by f.building_id"), p)}
        room_rows = session.execute(text(
            "select r.name, r.name_ar, r.kind, r.team_department, f.building_id from office_rooms r "
            "join office_floors f on f.tenant_id = r.tenant_id and f.workspace_id = r.workspace_id and f.id = r.floor_id "
            "where r.tenant_id = :t and r.workspace_id = :w order by f.building_id, f.level, r.name, r.id limit :n"),
            {**p, "n": MAX_BOARD_ROOMS}).all()
        departments = [r.team_department for r in session.execute(text(
            "select distinct team_department from office_rooms where tenant_id = :t and workspace_id = :w "
            "and team_department is not null order by team_department limit :n"), {**p, "n": MAX_DEPARTMENTS})]
        evidence = load_flow_evidence(
            session, context, now, th, can_leads=False, can_orders=False,
            can_approvals=has_permission(context, Permission.APPROVAL_READ))
    derived = derive_flow(evidence, th)
    listed = building_rows[:MAX_BOARD_BUILDINGS]
    ids = [r.id for r in listed]
    return {
        "workspace_id": context.workspace_id,
        "accessible": True,
        "as_of": now.isoformat(),
        "buildings": [
            {"id": r.id, "name": r.name, "name_ar": r.name_ar, "floors": floors.get(r.id, 0), "rooms": rooms_per.get(r.id, 0),
             "sign": _sign(r)}
            for r in listed
        ],
        "buildings_total": counts.buildings,
        "rooms": {
            "total": counts.rooms,
            "items": [
                {"name": r.name, "name_ar": r.name_ar, "kind": r.kind, "team_department": r.team_department, "building_id": r.building_id}
                for r in room_rows
            ],
        },
        "departments": departments,
        "work": work_summary(derived["items"], ids, source=evidence.sources.get("tasks", "not_connected"),
                             truncated=bool(derived["truncated"])),
    }


def _memberships(context: TenantContext):
    from .membership_directory import list_caller_memberships as read

    return read(context)


@router.get("/campus/boards")
def get_campus_boards(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE,
    cursor: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    now = utc_now()
    readable = []
    for member in _memberships(context):
        if member.workspace_id == context.workspace_id:
            continue  # the open workspace: its own live reads (registry, presence, flow) already carry its boards
        member_ctx = TenantContext(
            tenant_id=context.tenant_id, workspace_id=member.workspace_id,
            user_id=context.user_id, roles=[member.role.lower()],
        )
        if has_permission(member_ctx, Permission.TASK_READ):
            readable.append((member, member_ctx))
    start = 0
    if cursor is not None:
        index = next((i for i, (m, _) in enumerate(readable) if m.workspace_id == cursor), None)
        if index is None:
            raise HTTPException(400, "Unknown cursor; read the boards again from the start")
        start = index + 1
    page = readable[start:start + limit]
    boards: list[dict[str, Any]] = []
    for member, member_ctx in page:
        key = (context.tenant_id, member.workspace_id, context.user_id, member.role.lower())
        board = _cached(key)
        if board is None:
            try:
                board = read_board(member_ctx, now)
            except Exception:  # one unreadable workspace must not hide the others
                logger.exception("office_campus_board_unreadable")
                boards.append({"workspace_id": member.workspace_id, "accessible": False})
                continue
            _store(key, board)
        boards.append(board)
    more = start + limit < len(readable)
    return {
        "version": BOARDS_VERSION,
        "tenant_id": context.tenant_id,
        "current_workspace_id": context.workspace_id,
        "generated_at": now.isoformat(),
        "next_cursor": page[-1][0].workspace_id if more and page else None,
        "boards": boards,
    }
