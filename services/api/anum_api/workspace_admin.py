"""Workspace lifecycle: create another workspace, rename, archive and restore.

* ``POST /api/v1/organization/workspaces`` creates a workspace in the caller's tenant
  and makes the caller its first owner. Owners of the request's workspace and
  organization administrators may do it.
* ``PATCH /api/v1/workspaces/current``, ``POST /api/v1/workspaces/current/archive`` and
  ``/restore`` change the request's workspace: owners (``workspace:manage``) and
  organization administrators with a membership in it.
* An archived workspace stays readable but refuses every other write with ``409``
  (``dependencies.refuse_writes_to_archived_workspace``) until it is restored.
* ``GET /api/v1/workspaces/current/permissions`` tells clients what the caller may do
  here, so they show or hide controls from the API's answer instead of guessing.

Every change writes an audit record and a canonical event in the same transaction.
See docs/identity.md#workspace-lifecycle.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import (
    _unit_of_work,
    has_permission,
    is_organization_admin,
    repository_context,
    require_owner_or_org_admin,
    tenant_context,
)
from .events import CanonicalEventName
from .identity import is_valid_scope_id
from .repository import AnumRepository
from .schemas import TenantContext, Workspace, WorkspaceMembership, new_id, utc_now
from .workspace_members import _record_change

router = APIRouter(prefix="/api/v1", tags=["workspaces"])

MANAGE_REQUIRED = (
    "Changing this workspace requires the owner role in it or the organization "
    "administrator role. Ask a workspace owner to make the change or to make you an owner."
)
CREATE_REQUIRED = (
    "Creating a workspace requires the owner role in your current workspace or the "
    "organization administrator role. Ask an owner or your organization administrator."
)


class WorkspaceName(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a workspace name")
        return value


class WorkspaceCreateRequest(WorkspaceName):
    # Optional: generated from the name when left out. Same shape as x-workspace-id.
    workspace_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,80}$")


class WorkspaceArchiveRequest(BaseModel):
    # The workspace's current name, typed by the owner to confirm.
    confirm_name: str = Field(min_length=1, max_length=160)


class WorkspacePermissions(BaseModel):
    workspace_id: str
    role: str | None
    organization_admin: bool
    can_manage_workspace: bool
    can_create_workspace: bool
    can_manage_members: bool
    can_read_audit: bool
    can_export_audit: bool


def generated_workspace_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40] or "workspace"
    candidate = f"ws_{slug}_{secrets.token_hex(4)}"
    return candidate if is_valid_scope_id(candidate) else f"ws_{secrets.token_hex(8)}"


def _can(context: TenantContext, permission: Permission) -> bool:
    if has_permission(context, permission):
        return True
    return is_organization_admin(context) and has_permission(context, Permission.ORGANIZATION_READ)


def _current(repository: AnumRepository, context: TenantContext) -> Workspace:
    workspace = repository.get_workspace(context.workspace_id, context)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.get("/workspaces/current/permissions", response_model=WorkspacePermissions)
async def workspace_permissions(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspacePermissions:
    """What the caller may do in the request's workspace. Any member may ask."""
    membership = repository.get_membership(context)
    roles = [role for role in context.roles if role in ("owner", "member", "viewer")]
    return WorkspacePermissions(
        workspace_id=context.workspace_id,
        role=membership.role if membership is not None else (roles[0] if roles else None),
        organization_admin=is_organization_admin(context),
        can_manage_workspace=_can(context, Permission.WORKSPACE_MANAGE),
        can_create_workspace=_can(context, Permission.WORKSPACE_CREATE),
        can_manage_members=has_permission(context, Permission.MEMBERSHIP_MANAGE),
        can_read_audit=_can(context, Permission.AUDIT_READ),
        can_export_audit=_can(context, Permission.AUDIT_EXPORT),
    )


@router.post("/organization/workspaces", response_model=Workspace, status_code=status.HTTP_201_CREATED)
async def create_workspace_in_organization(
    payload: WorkspaceCreateRequest,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Workspace:
    """Create a workspace in the caller's tenant with the caller as its first owner."""
    require_owner_or_org_admin(context, Permission.WORKSPACE_CREATE, CREATE_REQUIRED)
    from .billing.enforce import require_capacity

    await asyncio.to_thread(require_capacity, context, "workspaces", 1)
    workspace_id = payload.workspace_id or generated_workspace_id(payload.name)
    if workspace_id == context.workspace_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That workspace ID is already in use")
    now = utc_now()
    target = context.model_copy(update={"workspace_id": workspace_id, "roles": ["owner"]})
    try:
        # Its own transaction, in the new workspace's RLS scope.
        with _unit_of_work(target, require_membership=False) as created_in:
            if repository.get_tenant(context.tenant_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Finish onboarding your organization before creating more workspaces",
                )
            workspace = created_in.create_workspace(
                Workspace(id=workspace_id, tenant_id=context.tenant_id, name=payload.name, created_at=now, updated_at=now)
            )
            created_in.save_membership(
                WorkspaceMembership(
                    tenant_id=context.tenant_id,
                    workspace_id=workspace_id,
                    user_id=context.user_id,
                    role="owner",
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            _record_change(
                created_in,
                target,
                event_type=CanonicalEventName.WORKSPACE_CREATED,
                audit_action="workspace.create",
                target=workspace_id,
                payload={"workspace_id": workspace_id, "name": payload.name, "created_from": context.workspace_id},
                now=now,
            )
    except (ValueError, IntegrityError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That workspace ID is already in use") from None
    # The source workspace's log also records who created which workspace from it.
    repository.record_audit(_source_audit(context, workspace_id, payload.name, now))
    return workspace


def _source_audit(context: TenantContext, workspace_id: str, name: str, now: datetime) -> AuditRecord:
    return AuditRecord(
        id=new_id("audit"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor=context.user_id,
        action="workspace.create",
        target=workspace_id,
        outcome="success",
        correlation_id=new_id("correlation"),
        created_at=now,
        metadata={"workspace_id": workspace_id, "name": name},
    )


@router.patch("/workspaces/current", response_model=Workspace)
async def rename_workspace(
    payload: WorkspaceName,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Workspace:
    require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, MANAGE_REQUIRED)
    workspace = _current(repository, context)
    if workspace.name == payload.name:
        return workspace
    now = utc_now()
    previous = workspace.name
    workspace = repository.save_workspace(workspace.model_copy(update={"name": payload.name, "updated_at": now}))
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_RENAMED,
        audit_action="workspace.rename",
        target=workspace.id,
        payload={"workspace_id": workspace.id, "previous_name": previous, "name": workspace.name},
        now=now,
    )
    return workspace


@router.post("/workspaces/current/archive", response_model=Workspace)
async def archive_workspace(
    payload: WorkspaceArchiveRequest,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Workspace:
    require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, MANAGE_REQUIRED)
    workspace = _current(repository, context)
    if payload.confirm_name.strip() != workspace.name:
        raise HTTPException(
            status_code=422,
            detail="Type the workspace's current name exactly to confirm archiving it",
        )
    now = utc_now()
    workspace = repository.save_workspace(
        workspace.model_copy(update={"archived_at": now, "archived_by": context.user_id, "updated_at": now})
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_ARCHIVED,
        audit_action="workspace.archive",
        target=workspace.id,
        payload={"workspace_id": workspace.id},
        now=now,
    )
    return workspace


@router.post("/workspaces/current/restore", response_model=Workspace)
async def restore_workspace(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Workspace:
    require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, MANAGE_REQUIRED)
    workspace = _current(repository, context)
    if workspace.archived_at is None:
        return workspace
    now = utc_now()
    archived_at = workspace.archived_at
    workspace = repository.save_workspace(
        workspace.model_copy(update={"archived_at": None, "archived_by": None, "updated_at": now})
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_RESTORED,
        audit_action="workspace.restore",
        target=workspace.id,
        payload={"workspace_id": workspace.id, "archived_at": archived_at.isoformat()},
        now=now,
    )
    return workspace
