"""ANUM MINE campus: one building per workspace the signed-in person actively belongs to.

``GET /api/v1/office/campus`` lists the caller's active memberships (read through the
narrow membership directory role) and then reads each workspace in its OWN tenant and
workspace RLS context with the application role: one short session per workspace, never a
cross-tenant or cross-workspace query. A workspace where the caller lacks ``task:read`` is
not listed. A workspace that fails to read is listed with ``accessible: false`` and no
numbers (nothing is guessed). ``PUT /api/v1/office/campus/profile`` lets an owner say what
kind of building the current workspace is. See docs/ANUM_MINE_PRESENCE_AND_CAMPUS.md.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_presence import derive_presence, load_evidence, totals
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, new_id, utc_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office", tags=["office campus"])

CAMPUS_VERSION = 1
MAX_BUILDINGS = 50
MAX_FLOORS = 50
KINDS = ("executive", "business", "department", "personal", "career")
ICONS = ("building", "briefcase", "store", "truck", "home", "user", "landmark", "factory", "shield", "chart")


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["executive", "business", "department", "personal", "career"]
    icon: Literal["building", "briefcase", "store", "truck", "home", "user", "landmark", "factory", "shield", "chart"]
    expected_revision: Annotated[int | None, Field(ge=1)] = None


def _memberships(context: TenantContext):
    from .membership_directory import list_caller_memberships as read

    return read(context)


def _building(context: TenantContext, name: str | None, role: str, archived: bool, now) -> dict[str, Any]:
    ctx = context
    with workspace_session(ctx) as session:
        p = {"t": ctx.tenant_id, "w": ctx.workspace_id}
        profile = session.execute(text(
            "select kind, icon, revision from campus_workspace_profiles where tenant_id = :t and workspace_id = :w"), p).first()
        floors = [
            {"id": r.id, "level": r.level, "name": r.name, "name_ar": r.name_ar, "building_id": r.building_id}
            for r in session.execute(text(
                "select id, level, name, name_ar, building_id from office_floors where tenant_id = :t and workspace_id = :w "
                "order by level, id limit :n"), {**p, "n": MAX_FLOORS})
        ]
        counts = session.execute(text(
            "select (select count(*) from office_buildings where tenant_id = :t and workspace_id = :w) as buildings, "
            "(select count(*) from office_rooms where tenant_id = :t and workspace_id = :w) as rooms, "
            "(select count(*) from office_desks where tenant_id = :t and workspace_id = :w) as desks"), p).one()
        items = derive_presence(load_evidence(session, ctx, now))
    state_totals = totals(items)
    verified = [i["last_verified_at"] for i in items if i["state"] != "idle"]
    return {
        "workspace_id": ctx.workspace_id,
        "name": name or ctx.workspace_id,
        "kind": profile.kind if profile else None,
        "icon": profile.icon if profile else None,
        "profile_revision": profile.revision if profile else None,
        "role": role,
        "archived": archived,
        "accessible": True,
        "floors": floors,
        "office_buildings": counts.buildings,
        "rooms": counts.rooms,
        "desks": counts.desks,
        "agents": {"total": len(items), "by_state": state_totals},
        # The newest evidence time behind a non-idle state; null when every agent is idle or none exist.
        "presence_verified_at": max(verified) if verified else None,
        "presence_as_of": now.isoformat(),
    }


@router.get("/campus")
def get_campus(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    now = utc_now()
    buildings: list[dict[str, Any]] = []
    members = _memberships(context)
    listed = 0
    truncated = False
    for member in members:
        member_ctx = TenantContext(
            tenant_id=context.tenant_id, workspace_id=member.workspace_id,
            user_id=context.user_id, roles=[member.role.lower()],
        )
        if not has_permission(member_ctx, Permission.TASK_READ):
            continue  # not accessible: not listed
        if listed >= MAX_BUILDINGS:
            truncated = True
            break
        listed += 1
        try:
            buildings.append(_building(member_ctx, member.workspace_name, member.role, member.archived, now))
        except Exception:  # one unreadable workspace must not hide the others
            logger.exception("office_campus_workspace_unreadable")
            buildings.append({
                "workspace_id": member.workspace_id, "name": member.workspace_name or member.workspace_id,
                "kind": None, "icon": None, "profile_revision": None, "role": member.role,
                "archived": member.archived, "accessible": False, "floors": [], "office_buildings": 0,
                "rooms": 0, "desks": 0, "agents": None, "presence_verified_at": None, "presence_as_of": None,
            })
    return {
        "version": CAMPUS_VERSION,
        "tenant_id": context.tenant_id,
        "current_workspace_id": context.workspace_id,
        "generated_at": now.isoformat(),
        "truncated": truncated,
        "buildings": buildings,
    }


@router.put("/campus/profile")
def put_profile(body: ProfileBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Owner-only: what kind of building the current workspace is, with an optimistic revision."""
    require_permission(context, Permission.WORKSPACE_MANAGE)
    now = utc_now()
    p = {"t": context.tenant_id, "w": context.workspace_id, "k": body.kind, "i": body.icon,
         "u": context.user_id, "now": now}
    with workspace_session(context, commit=True) as session:
        current = session.execute(text(
            "select revision from campus_workspace_profiles where tenant_id = :t and workspace_id = :w for update"), p).first()
        if current is None:
            if body.expected_revision is not None:
                raise HTTPException(409, "Profile does not exist yet; send no expected_revision")
            session.execute(text(
                "insert into campus_workspace_profiles (tenant_id, workspace_id, kind, icon, revision, updated_by, created_at, updated_at) "
                "values (:t, :w, :k, :i, 1, :u, :now, :now)"), p)
            revision = 1
        else:
            if body.expected_revision != current.revision:
                raise HTTPException(409, "The profile changed; reload and try again")
            revision = current.revision + 1
            session.execute(text(
                "update campus_workspace_profiles set kind = :k, icon = :i, revision = :r, updated_by = :u, updated_at = :now "
                "where tenant_id = :t and workspace_id = :w"), {**p, "r": revision})
        from .db.repository import SqlAlchemyRepository

        SqlAlchemyRepository(session).record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action="office.campus.profile", target=context.workspace_id,
            outcome="success", correlation_id=new_id("correlation"), created_at=now,
            metadata={"kind": body.kind, "icon": body.icon},
        ))
    return {"workspace_id": context.workspace_id, "kind": body.kind, "icon": body.icon, "revision": revision}
