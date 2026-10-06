"""Workspace invitations and membership management.

Owners invite people with a single-use, expiring token (only its SHA-256 hash is
stored), list members, change roles, and deactivate or reactivate members. The last
active owner can never be demoted or deactivated. Every change writes an audit record
and a canonical event through the request's repository, in the same transaction, so
the event reaches the event bus only if the change commits.

See docs/identity.md (Invitations and membership management).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from .audit import AuditRecord
from .authorization import Permission, Role
from .dependencies import (
    AuthenticatedIdentity,
    authenticated_identity,
    identity_repository_context,
    repository_context,
    require_permission,
    tenant_context,
)
from .events import CanonicalEventName, create_event
from .repository import AnumRepository
from .schemas import (
    InvitationStatus,
    TenantContext,
    WorkspaceInvitation,
    WorkspaceMembership,
    new_id,
    utc_now,
)

INVITATION_PREFIX = "anum_inv_"
DEFAULT_TTL_HOURS = 168
MAX_TTL_HOURS = 720

router = APIRouter(prefix="/api/v1", tags=["workspace-members"])


# --------------------------------------------------------------------------- tokens


def new_invitation_token() -> str:
    return f"{INVITATION_PREFIX}{secrets.token_urlsafe(32)}"


def hash_invitation_token(token: str) -> str:
    """SHA-256 of a 256-bit random token; a slow KDF adds nothing at that entropy."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().casefold()


# --------------------------------------------------------------------------- payloads


class InvitationCreate(BaseModel):
    role: Role
    invitee_user_id: str | None = Field(default=None, min_length=1, max_length=120)
    invitee_email: str | None = Field(default=None, min_length=3, max_length=320)
    ttl_hours: int = Field(default=DEFAULT_TTL_HOURS, ge=1, le=MAX_TTL_HOURS)

    @field_validator("invitee_email")
    @classmethod
    def _email_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        local, at, domain = value.partition("@")
        if not at or not local or "." not in domain or any(c.isspace() for c in value):
            raise ValueError("invitee_email is not an email address")
        return value

    @model_validator(mode="after")
    def _needs_invitee(self) -> InvitationCreate:
        if self.invitee_user_id is None and self.invitee_email is None:
            raise ValueError("Set invitee_user_id, invitee_email, or both")
        return self


class InvitationView(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    role: str
    invitee_user_id: str | None
    invitee_email: str | None
    status: InvitationStatus
    created_by_user_id: str
    expires_at: datetime
    accepted_by_user_id: str | None
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreated(BaseModel):
    invitation: InvitationView
    # Returned exactly once. Only its hash is stored, so it cannot be shown again.
    token: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=len(INVITATION_PREFIX) + 1, max_length=200)


class InvitationAccepted(BaseModel):
    invitation: InvitationView
    membership: WorkspaceMembership


class MemberRoleUpdate(BaseModel):
    role: Role


# --------------------------------------------------------------------------- helpers


def invitation_view(invitation: WorkspaceInvitation, now: datetime | None = None) -> InvitationView:
    current = now or utc_now()
    shown = invitation.status
    if shown == InvitationStatus.PENDING and invitation.expires_at <= current:
        shown = InvitationStatus.EXPIRED
    return InvitationView(
        id=invitation.id,
        tenant_id=invitation.tenant_id,
        workspace_id=invitation.workspace_id,
        role=invitation.role,
        invitee_user_id=invitation.invitee_user_id,
        invitee_email=invitation.invitee_email,
        status=shown,
        created_by_user_id=invitation.created_by_user_id,
        expires_at=invitation.expires_at,
        accepted_by_user_id=invitation.accepted_by_user_id,
        accepted_at=invitation.accepted_at,
        revoked_at=invitation.revoked_at,
        created_at=invitation.created_at,
    )


def _record_change(
    repository: AnumRepository,
    context: TenantContext,
    *,
    event_type: CanonicalEventName,
    audit_action: str,
    target: str,
    payload: dict[str, Any],
    audit_metadata: dict[str, Any] | None = None,
    now: datetime,
) -> None:
    correlation_id = new_id("correlation")
    repository.record_event(
        create_event(
            event_type,
            context,
            target,
            payload,
            correlation_id=correlation_id,
            created_at=now,
        ).event
    )
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=audit_action,
            target=target,
            outcome="success",
            correlation_id=correlation_id,
            created_at=now,
            metadata={**payload, **(audit_metadata or {})},
        )
    )


def _require_manager(context: TenantContext) -> None:
    require_permission(context, Permission.MEMBERSHIP_MANAGE)


def _ensure_not_last_owner(
    repository: AnumRepository, context: TenantContext, member: WorkspaceMembership
) -> None:
    if member.role != Role.OWNER.value or not member.active:
        return
    owners = repository.list_active_owners_for_update(context)
    if all(owner.user_id == member.user_id for owner in owners):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active owner cannot be demoted or deactivated",
        )


def _member_or_404(
    repository: AnumRepository, context: TenantContext, user_id: str
) -> WorkspaceMembership:
    # Lock the owner rows first, always in the same order, then the target. Taking the
    # target first could deadlock two owners demoting each other at the same moment.
    repository.list_active_owners_for_update(context)
    member = repository.get_member_for_update(user_id, context)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return member.model_copy(deep=True)


# --------------------------------------------------------------------------- invitations


@router.post(
    "/workspace-invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    payload: InvitationCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> InvitationCreated:
    _require_manager(context)
    if repository.get_workspace(context.workspace_id, context) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    now = utc_now()
    token = new_invitation_token()
    invitation = repository.save_invitation(
        WorkspaceInvitation(
            id=new_id("invitation"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            role=payload.role.value,
            invitee_user_id=payload.invitee_user_id,
            invitee_email=payload.invitee_email,
            token_hash=hash_invitation_token(token),
            created_by_user_id=context.user_id,
            expires_at=now + timedelta(hours=payload.ttl_hours),
            created_at=now,
            updated_at=now,
        )
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_INVITATION_CREATED,
        audit_action="workspace_invitation.create",
        target=invitation.id,
        payload={
            "invitation_id": invitation.id,
            "role": invitation.role,
            "invitee_user_id": invitation.invitee_user_id,
            "email_bound": invitation.invitee_email is not None,
            "expires_at": invitation.expires_at.isoformat(),
        },
        audit_metadata={"invitee_email": invitation.invitee_email},
        now=now,
    )
    return InvitationCreated(invitation=invitation_view(invitation, now), token=token)


@router.get("/workspace-invitations", response_model=list[InvitationView])
async def list_invitations(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[InvitationView]:
    _require_manager(context)
    now = utc_now()
    return [invitation_view(invitation, now) for invitation in repository.list_invitations(context)]


@router.post("/workspace-invitations/{invitation_id}/revoke", response_model=InvitationView)
async def revoke_invitation(
    invitation_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> InvitationView:
    _require_manager(context)
    invitation = repository.get_invitation_for_update(invitation_id, context)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invitation.status != InvitationStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invitation is already {invitation.status.value}",
        )
    now = utc_now()
    invitation = repository.save_invitation(
        invitation.model_copy(
            update={"status": InvitationStatus.REVOKED, "revoked_at": now, "updated_at": now}
        )
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_INVITATION_REVOKED,
        audit_action="workspace_invitation.revoke",
        target=invitation.id,
        payload={"invitation_id": invitation.id},
        now=now,
    )
    return invitation_view(invitation, now)


@router.post("/workspace-invitations/accept", response_model=InvitationAccepted)
async def accept_invitation(
    payload: InvitationAccept,
    identity: AuthenticatedIdentity = Depends(authenticated_identity),
    repository: AnumRepository = Depends(identity_repository_context),
) -> InvitationAccepted:
    """Redeem a token for a membership in the workspace selected by ``x-workspace-id``.

    The lookup runs inside the caller's tenant and workspace scope (RLS), so a token
    issued for another tenant or workspace is not found. A token is consumed only on
    success: a wrong caller, an expired or a revoked token leaves it unchanged.
    """
    context = identity.context
    if not payload.token.startswith(INVITATION_PREFIX):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    invitation = repository.find_invitation_by_token_hash_for_update(
        hash_invitation_token(payload.token), context
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation was already used")
    if invitation.status == InvitationStatus.REVOKED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation was revoked")
    now = utc_now()
    if invitation.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has expired")
    if invitation.invitee_user_id is not None and invitation.invitee_user_id != context.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invitation is for a different user",
        )
    if invitation.invitee_email is not None and not (
        identity.email
        and identity.email_verified
        and normalize_email(identity.email) == normalize_email(invitation.invitee_email)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invitation is for a different verified email address",
        )

    existing = repository.get_member_for_update(context.user_id, context)
    if existing is not None and existing.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already an active member of this workspace",
        )
    membership = repository.save_membership(
        WorkspaceMembership(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            role=invitation.role,
            active=True,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
    )
    invitation = repository.save_invitation(
        invitation.model_copy(
            update={
                "status": InvitationStatus.ACCEPTED,
                "accepted_by_user_id": context.user_id,
                "accepted_at": now,
                "updated_at": now,
            }
        )
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_INVITATION_ACCEPTED,
        audit_action="workspace_invitation.accept",
        target=invitation.id,
        payload={"invitation_id": invitation.id, "user_id": context.user_id},
        now=now,
    )
    _record_change(
        repository,
        context,
        event_type=(
            CanonicalEventName.WORKSPACE_MEMBER_REACTIVATED
            if existing is not None
            else CanonicalEventName.WORKSPACE_MEMBER_ADDED
        ),
        audit_action="workspace_member.add" if existing is None else "workspace_member.reactivate",
        target=context.user_id,
        payload={
            "user_id": context.user_id,
            "role": membership.role,
            "invitation_id": invitation.id,
        },
        now=now,
    )
    return InvitationAccepted(invitation=invitation_view(invitation, now), membership=membership)


# --------------------------------------------------------------------------- members


@router.get("/workspace-members", response_model=list[WorkspaceMembership])
async def list_members(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[WorkspaceMembership]:
    _require_manager(context)
    return repository.list_memberships(context)


def _validated_user_id(user_id: str) -> str:
    if not user_id or len(user_id) > 120:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return user_id


@router.put("/workspace-members/{user_id}/role", response_model=WorkspaceMembership)
async def change_member_role(
    user_id: str,
    payload: MemberRoleUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspaceMembership:
    _require_manager(context)
    member = _member_or_404(repository, context, _validated_user_id(user_id))
    if member.role == payload.role.value:
        return member
    if payload.role != Role.OWNER:
        _ensure_not_last_owner(repository, context, member)
    now = utc_now()
    previous = member.role
    member = repository.save_membership(
        member.model_copy(update={"role": payload.role.value, "updated_at": now})
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_MEMBER_ROLE_CHANGED,
        audit_action="workspace_member.role_change",
        target=member.user_id,
        payload={"user_id": member.user_id, "previous_role": previous, "role": member.role},
        now=now,
    )
    return member


@router.post("/workspace-members/{user_id}/deactivate", response_model=WorkspaceMembership)
async def deactivate_member(
    user_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspaceMembership:
    _require_manager(context)
    member = _member_or_404(repository, context, _validated_user_id(user_id))
    if not member.active:
        return member
    _ensure_not_last_owner(repository, context, member)
    now = utc_now()
    member = repository.save_membership(
        member.model_copy(update={"active": False, "updated_at": now})
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_MEMBER_DEACTIVATED,
        audit_action="workspace_member.deactivate",
        target=member.user_id,
        payload={"user_id": member.user_id, "role": member.role},
        now=now,
    )
    return member


@router.post("/workspace-members/{user_id}/reactivate", response_model=WorkspaceMembership)
async def reactivate_member(
    user_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> WorkspaceMembership:
    _require_manager(context)
    member = _member_or_404(repository, context, _validated_user_id(user_id))
    if member.active:
        return member
    now = utc_now()
    member = repository.save_membership(
        member.model_copy(update={"active": True, "updated_at": now})
    )
    _record_change(
        repository,
        context,
        event_type=CanonicalEventName.WORKSPACE_MEMBER_REACTIVATED,
        audit_action="workspace_member.reactivate",
        target=member.user_id,
        payload={"user_id": member.user_id, "role": member.role},
        now=now,
    )
    return member

