"""Reception visitor register (docs/ANUM_MINE_HQ_RECEPTION.md, owner decision O4).

One row per visit in ``office_visitors`` (forced workspace RLS, purge policy, tenant lifecycle table):

* ``GET    /api/v1/office/visitors``: paged list (keyset cursor), text search, status filter and an optional day
  window (``since``/``until``: visits expected or checked in inside it, plus everyone still checked in).
  ``task:read``. Answers ``can_manage`` so the web can explain who may add visitors.
* ``GET    /api/v1/office/visitors/summary``: only the number of visitors checked in now, by room and building.
  No names. The 3D reception room reads this.
* ``POST   /api/v1/office/visitors``: add a visit (``arrived: true`` checks a walk-in in at once).
* ``PATCH  /api/v1/office/visitors/{id}``: change the details of an open visit.
* ``POST   /api/v1/office/visitors/{id}/check-in`` | ``/check-out`` | ``/cancel``: the status moves.
* ``DELETE /api/v1/office/visitors/{id}``: remove a record.

Every write needs ``workspace:manage`` (an owner-level office write, like the office builder), ``input_mode: "click"``
(a spoken or typed answer never changes the register and no registered agent tool calls these routes) and, for a
change to an existing visit, the ``expected_revision`` the person was shown. Each write leaves one audit record with
ids, statuses and counts only (never the visitor's name, company or purpose) and, after the commit, one
``office.visitors_changed`` event with ids only. Times are server times.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import event_runtime, has_permission, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/office/visitors", tags=["office reception"])

VERSION = 1
MAX_PAGE = 100
WINDOW_DAYS = 400
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
VisitId = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
HostId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@+-]*$")]
InputMode = Literal["click", "typed", "voice"]
Status = Literal["expected", "checked_in", "checked_out", "cancelled"]
STATUSES: tuple[str, ...] = ("expected", "checked_in", "checked_out", "cancelled")

CLICK_ONLY = "Confirm this on screen with a click. A spoken or typed answer cannot change the visitor register."
NOT_FOUND = "Visit not found"
STALE = "The visit changed; reload and try again"
EXISTS = "A visit with this id already exists"
CLOSED = "A finished visit cannot be changed; delete it or add a new visit"
NOT_EXPECTED_IN = "Only an expected visitor can be checked in"
NOT_IN = "Only a checked-in visitor can be checked out"
NOT_EXPECTED_CANCEL = "Only an expected visit can be cancelled"
BAD_PLACE = "That building or room does not exist in this workspace"
ROOM_ELSEWHERE = "The room is not in that building"
BAD_HOST = "The host is not an active member or a visible agent of this workspace"
HOST_PAIR = "Give both the host kind and the host, or neither"
BAD_TIME = "The expected time must have a time zone and be within 400 days of today"
BAD_WINDOW = "Give both since and until, with since before until"
BAD_CURSOR = "Unknown or invalid cursor"
REQUIRED = "The visitor's name, the purpose and the expected time cannot be empty"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")

_COLUMNS = ("id, building_id, room_id, visitor_name, company, host_kind, host_id, purpose, expected_at, checked_in_at, "
            "checked_out_at, cancelled_at, status, revision, created_by, updated_by, created_at, updated_at")


def _clean(value: str | None, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("required")
        return None
    if _CONTROL.search(value):
        raise ValueError("must not contain control characters")
    cleaned = _SPACES.sub(" ", value).strip()
    if not cleaned:
        if required:
            raise ValueError("must not be empty")
        return None
    return cleaned


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisitCreate(_Strict):
    id: Identifier | None = None
    visitor_name: str = Field(min_length=1, max_length=120)
    company: str | None = Field(default=None, max_length=120)
    host_kind: Literal["user", "agent"] | None = None
    host_id: HostId | None = None
    purpose: str = Field(min_length=1, max_length=200)
    expected_at: datetime
    building_id: Identifier | None = None
    room_id: Identifier | None = None
    arrived: bool = False
    input_mode: InputMode = "typed"

    @field_validator("visitor_name", "purpose")
    @classmethod
    def _required(cls, value: str) -> str:
        return _clean(value, required=True) or ""

    @field_validator("company")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _clean(value, required=False)


class VisitUpdate(_Strict):
    visitor_name: str | None = Field(default=None, min_length=1, max_length=120)
    company: str | None = Field(default=None, max_length=120)
    host_kind: Literal["user", "agent"] | None = None
    host_id: HostId | None = None
    purpose: str | None = Field(default=None, min_length=1, max_length=200)
    expected_at: datetime | None = None
    building_id: Identifier | None = None
    room_id: Identifier | None = None
    expected_revision: int = Field(ge=1)
    input_mode: InputMode = "typed"

    @field_validator("visitor_name", "purpose")
    @classmethod
    def _required(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value, required=True)

    @field_validator("company")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _clean(value, required=False)


class VisitMove(_Strict):
    expected_revision: int = Field(ge=1)
    input_mode: InputMode = "typed"


# --------------------------------------------------------------------------- helpers


def _click(mode: str) -> None:
    if mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN, CLICK_ONLY)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _view(row: Any) -> dict[str, Any]:
    r = row._mapping
    return {
        "id": r["id"], "visitor_name": r["visitor_name"], "company": r["company"],
        "host": {"kind": r["host_kind"], "id": r["host_id"]} if r["host_kind"] else None,
        "purpose": r["purpose"], "status": r["status"],
        "expected_at": _iso(r["expected_at"]), "checked_in_at": _iso(r["checked_in_at"]),
        "checked_out_at": _iso(r["checked_out_at"]), "cancelled_at": _iso(r["cancelled_at"]),
        "building_id": r["building_id"], "room_id": r["room_id"], "revision": r["revision"],
        "created_by": r["created_by"], "updated_by": r["updated_by"],
        "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
    }


def _check_time(value: datetime, now: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None or abs(value - now) > timedelta(days=WINDOW_DAYS):
        raise HTTPException(422, BAD_TIME)


def _scope(context: TenantContext) -> dict[str, Any]:
    return {"t": context.tenant_id, "w": context.workspace_id}


def _place(session: Any, context: TenantContext, building_id: str | None, room_id: str | None) -> tuple[str | None, str | None]:
    """The building and room of a visit, checked in this workspace. A room alone gives its own building."""
    p = _scope(context)
    if room_id is not None:
        row = session.execute(text(
            "select f.building_id from office_rooms r join office_floors f on f.tenant_id = r.tenant_id "
            "and f.workspace_id = r.workspace_id and f.id = r.floor_id "
            "where r.tenant_id = :t and r.workspace_id = :w and r.id = :r"), {**p, "r": room_id}).first()
        if row is None:
            raise HTTPException(422, BAD_PLACE)
        if building_id is not None and building_id != row.building_id:
            raise HTTPException(422, ROOM_ELSEWHERE)
        return row.building_id, room_id
    if building_id is not None:
        found = session.execute(text("select 1 from office_buildings where tenant_id = :t and workspace_id = :w and id = :b"),
                                {**p, "b": building_id}).first()
        if found is None:
            raise HTTPException(422, BAD_PLACE)
    return building_id, None


def _host(session: Any, context: TenantContext, kind: str | None, host_id: str | None) -> None:
    if (kind is None) != (host_id is None):
        raise HTTPException(422, HOST_PAIR)
    if kind is None:
        return
    p = {**_scope(context), "h": host_id}
    if kind == "user":
        found = session.execute(text("select 1 from workspace_memberships where tenant_id = :t and workspace_id = :w "
                                     "and user_id = :h and active"), p).first()
    else:
        # agent_profiles RLS also hides another person's private agent (the session carries the user).
        found = session.execute(text("select 1 from agent_profiles where tenant_id = :t and workspace_id = :w and id = :h"), p).first()
    if found is None:
        raise HTTPException(422, BAD_HOST)


def _locked(session: Any, context: TenantContext, visit_id: str, revision: int) -> Any:
    row = session.execute(text(f"select {_COLUMNS} from office_visitors where tenant_id = :t and workspace_id = :w "  # nosec B608: fixed columns
                               "and id = :i for update"), {**_scope(context), "i": visit_id}).first()
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    if row.revision != revision:
        raise HTTPException(409, STALE)
    return row


def _reread(session: Any, context: TenantContext, visit_id: str) -> Any:
    return session.execute(text(f"select {_COLUMNS} from office_visitors where tenant_id = :t and workspace_id = :w and id = :i"),  # nosec B608: fixed columns
                           {**_scope(context), "i": visit_id}).one()


def _record(session: Any, context: TenantContext, action: str, row: Any, events: list[Any], **extra: Any) -> None:
    """Audit record and event: ids, statuses and counts only. Never the visitor's name, company or purpose."""
    from .db.repository import SqlAlchemyRepository

    r = row._mapping
    metadata: dict[str, Any] = {
        "office_resource": r["id"], "resource_kind": "visitors", "status": r["status"], "revision": r["revision"],
        "building_id": r["building_id"], "room_id": r["room_id"], "host_kind": r["host_kind"], "host_id": r["host_id"],
        "has_company": bool(r["company"]), "input_mode": "click", **extra,
    }
    repository = SqlAlchemyRepository(session)
    now = utc_now()
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=action, target=r["id"], outcome="success", correlation_id=new_id("correlation"), created_at=now,
        metadata=metadata,
    ))
    payload = {"action": action, "visitor_id": r["id"], "status": r["status"], "building_id": r["building_id"],
               "room_id": r["room_id"]}
    events.append(repository.record_event(create_event(CanonicalEventName.OFFICE_VISITORS_CHANGED, context, r["id"], payload).event))


def _writable(context: TenantContext, mode: str) -> None:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    _click(mode)


def _encode_cursor(expected_at: datetime, visit_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([expected_at.isoformat(), visit_id]).encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        when, visit_id = raw
        parsed = datetime.fromisoformat(when)
        if not isinstance(visit_id, str) or parsed.tzinfo is None:
            raise ValueError("cursor")
        return parsed, visit_id
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(422, BAD_CURSOR) from None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# --------------------------------------------------------------------------- reads


@router.get("")
def list_visits(
    context: TenantContext = Depends(tenant_context),
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 25,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    where = ["tenant_id = :t", "workspace_id = :w"]
    params: dict[str, Any] = _scope(context)
    if (since is None) != (until is None) or (since is not None and until is not None and (since.tzinfo is None or until.tzinfo is None or since >= until)):
        raise HTTPException(422, BAD_WINDOW)
    if since is not None:
        # The day's visits: expected or checked in inside the window, plus everyone who is still checked in.
        where.append("((expected_at >= :since and expected_at < :until) or (checked_in_at >= :since and checked_in_at < :until) "
                     "or status = 'checked_in')")
        params.update(since=since, until=until)
    text_q = _clean(q, required=False) if q else None
    if text_q:
        where.append("(visitor_name ilike :q escape '\\' or coalesce(company, '') ilike :q escape '\\' or purpose ilike :q escape '\\')")
        params["q"] = _like(text_q)
    base = " and ".join(where)
    page_where = base
    if status_filter:
        page_where += " and status = :s"
        params["s"] = status_filter
    if cursor:
        after_at, after_id = _decode_cursor(cursor)
        page_where += " and (expected_at, id) > (:ca, :ci)"
        params.update(ca=after_at, ci=after_id)
    with workspace_session(context) as session:
        rows = session.execute(text(f"select {_COLUMNS} from office_visitors where {page_where} "  # nosec B608: fixed clauses, bound values
                                    "order by expected_at, id limit :n"), {**params, "n": limit + 1}).all()
        counts = {s: 0 for s in STATUSES}
        for row in session.execute(text(f"select status, count(*) as n from office_visitors where {base} group by status"), params):  # nosec B608: fixed clauses
            counts[row.status] = row.n
    more = len(rows) > limit
    items = [_view(r) for r in rows[:limit]]
    next_cursor = _encode_cursor(rows[limit - 1].expected_at, rows[limit - 1].id) if more else None
    total = counts[status_filter] if status_filter else sum(counts.values())
    return {"version": VERSION, "items": items, "next_cursor": next_cursor, "total": total, "counts": counts,
            "can_manage": has_permission(context, Permission.WORKSPACE_MANAGE), "as_of": utc_now().isoformat()}


@router.get("/summary")
def summary(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """How many visitors are checked in now, by room (and by building for visits without a room). Counts only."""
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    by_room: dict[str, int] = {}
    by_building: dict[str, int] = {}
    unplaced = 0
    with workspace_session(context) as session:
        for row in session.execute(text("select room_id, building_id, count(*) as n from office_visitors where tenant_id = :t "
                                        "and workspace_id = :w and status = 'checked_in' group by room_id, building_id"), _scope(context)):
            if row.room_id:
                by_room[row.room_id] = by_room.get(row.room_id, 0) + row.n
            elif row.building_id:
                by_building[row.building_id] = by_building.get(row.building_id, 0) + row.n
            else:
                unplaced += row.n
    total = sum(by_room.values()) + sum(by_building.values()) + unplaced
    return {"version": VERSION, "checked_in": total,
            "by_room": [{"room_id": k, "count": v} for k, v in sorted(by_room.items())],
            "by_building": [{"building_id": k, "count": v} for k, v in sorted(by_building.items())],
            "unplaced": unplaced, "as_of": utc_now().isoformat()}


# --------------------------------------------------------------------------- writes


def _same(row: Any, body: VisitCreate, building_id: str | None, room_id: str | None) -> bool:
    r = row._mapping
    return (r["visitor_name"], r["company"], r["host_kind"], r["host_id"], r["purpose"], r["building_id"], r["room_id"]) == (
        body.visitor_name, body.company, body.host_kind, body.host_id, body.purpose, building_id, room_id,
    ) and r["expected_at"] == body.expected_at


@router.post("", status_code=201)
def create_visit(body: VisitCreate, response: Response, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _writable(context, body.input_mode)
    now = utc_now()
    _check_time(body.expected_at, now)
    visit_id = body.id or new_id("visit")
    events: list[Any] = []
    with workspace_session(context, commit=True) as session:
        building_id, room_id = _place(session, context, body.building_id, body.room_id)
        _host(session, context, body.host_kind, body.host_id)
        existing = session.execute(text(f"select {_COLUMNS} from office_visitors where tenant_id = :t and workspace_id = :w and id = :i"),  # nosec B608: fixed columns
                                   {**_scope(context), "i": visit_id}).first()
        if existing is not None:
            # A retry of the same create answers the stored visit; anything else under that id is refused.
            if _same(existing, body, building_id, room_id):
                response.status_code = 200
                return _view(existing)
            raise HTTPException(409, EXISTS)
        arrived = body.arrived
        session.execute(text(
            "insert into office_visitors (tenant_id, workspace_id, id, building_id, room_id, visitor_name, company, host_kind, "
            "host_id, purpose, expected_at, checked_in_at, status, revision, created_by, updated_by, created_at, updated_at) "
            "values (:t, :w, :i, :b, :r, :n, :c, :hk, :h, :p, :e, :ci, :s, 1, :u, :u, :now, :now)"),
            {**_scope(context), "i": visit_id, "b": building_id, "r": room_id, "n": body.visitor_name, "c": body.company,
             "hk": body.host_kind, "h": body.host_id, "p": body.purpose, "e": body.expected_at,
             "ci": now if arrived else None, "s": "checked_in" if arrived else "expected", "u": context.user_id, "now": now})
        row = _reread(session, context, visit_id)
        _record(session, context, "office.visitor.create", row, events, arrived=arrived)
    event_runtime.after_commit(events)
    return _view(row)


@router.patch("/{visit_id}")
def update_visit(visit_id: VisitId, body: VisitUpdate, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _writable(context, body.input_mode)
    given = body.model_fields_set - {"expected_revision", "input_mode"}
    for field in ("visitor_name", "purpose", "expected_at"):
        if field in given and getattr(body, field) is None:
            raise HTTPException(422, REQUIRED)
    now = utc_now()
    if body.expected_at is not None:
        _check_time(body.expected_at, now)
    events: list[Any] = []
    with workspace_session(context, commit=True) as session:
        before = _locked(session, context, visit_id, body.expected_revision)
        if before.status in ("checked_out", "cancelled"):
            raise HTTPException(409, CLOSED)
        b = before._mapping
        merged = {f: (getattr(body, f) if f in given else b[f]) for f in
                  ("visitor_name", "company", "host_kind", "host_id", "purpose", "expected_at", "building_id", "room_id")}
        if "host_kind" in given and "host_id" not in given and merged["host_kind"] is None:
            merged["host_id"] = None
        if "building_id" in given and "room_id" not in given and merged["building_id"] != b["building_id"]:
            merged["room_id"] = None  # a new building drops the old room
        building_id, room_id = _place(session, context, merged["building_id"], merged["room_id"])
        _host(session, context, merged["host_kind"], merged["host_id"])
        final = {**merged, "building_id": building_id, "room_id": room_id}
        changed = sorted(f for f in final if final[f] != b[f])
        if not changed:
            return _view(before)
        session.execute(text(
            "update office_visitors set visitor_name = :n, company = :c, host_kind = :hk, host_id = :h, purpose = :p, "
            "expected_at = :e, building_id = :b, room_id = :r, revision = revision + 1, updated_by = :u, updated_at = :now "
            "where tenant_id = :t and workspace_id = :w and id = :i"),
            {**_scope(context), "i": visit_id, "n": merged["visitor_name"], "c": merged["company"], "hk": merged["host_kind"],
             "h": merged["host_id"], "p": merged["purpose"], "e": merged["expected_at"], "b": building_id, "r": room_id,
             "u": context.user_id, "now": now})
        row = _reread(session, context, visit_id)
        _record(session, context, "office.visitor.update", row, events, changed=changed)
    event_runtime.after_commit(events)
    return _view(row)


_MOVES: dict[str, tuple[str, str, str, str]] = {
    # move: (from status, to status, time column, refusal)
    "check-in": ("expected", "checked_in", "checked_in_at", NOT_EXPECTED_IN),
    "check-out": ("checked_in", "checked_out", "checked_out_at", NOT_IN),
    "cancel": ("expected", "cancelled", "cancelled_at", NOT_EXPECTED_CANCEL),
}


def _move(context: TenantContext, visit_id: str, body: VisitMove, move: str) -> dict[str, Any]:
    _writable(context, body.input_mode)
    source, target, column, refusal = _MOVES[move]
    events: list[Any] = []
    with workspace_session(context, commit=True) as session:
        before = _locked(session, context, visit_id, body.expected_revision)
        if before.status != source:
            raise HTTPException(409, refusal)
        session.execute(text(f"update office_visitors set status = :s, {column} = :now, revision = revision + 1, "  # nosec B608: column from the fixed _MOVES table
                             "updated_by = :u, updated_at = :now where tenant_id = :t and workspace_id = :w and id = :i"),
                        {**_scope(context), "i": visit_id, "s": target, "u": context.user_id, "now": utc_now()})
        row = _reread(session, context, visit_id)
        _record(session, context, f"office.visitor.{move.replace('-', '_')}", row, events, previous_status=source)
    event_runtime.after_commit(events)
    return _view(row)


@router.post("/{visit_id}/check-in")
def check_in(visit_id: VisitId, body: VisitMove, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    return _move(context, visit_id, body, "check-in")


@router.post("/{visit_id}/check-out")
def check_out(visit_id: VisitId, body: VisitMove, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    return _move(context, visit_id, body, "check-out")


@router.post("/{visit_id}/cancel")
def cancel(visit_id: VisitId, body: VisitMove, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    return _move(context, visit_id, body, "cancel")


@router.delete("/{visit_id}", status_code=204)
def delete_visit(
    visit_id: VisitId,
    expected_revision: Annotated[int, Query(ge=1)],
    input_mode: InputMode = "typed",
    context: TenantContext = Depends(tenant_context),
) -> Response:
    _writable(context, input_mode)
    events: list[Any] = []
    with workspace_session(context, commit=True) as session:
        before = _locked(session, context, visit_id, expected_revision)
        session.execute(text("delete from office_visitors where tenant_id = :t and workspace_id = :w and id = :i"),
                        {**_scope(context), "i": visit_id})
        _record(session, context, "office.visitor.delete", before, events)
    event_runtime.after_commit(events)
    return Response(status_code=204)
