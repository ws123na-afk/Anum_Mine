"""The caller's own workspace memberships across the workspaces of their tenant.

``GET /api/v1/me/workspace-memberships`` lets clients (the web workspace switcher and
Flutter's workspace picker) list every workspace the caller may switch to. Clients still
confirm the membership in the target workspace before switching.

Tenant isolation without bypassing RLS (docs/identity.md#my-workspaces): the
tenant-isolation policies of ``workspace_memberships`` and ``workspaces`` admit one
workspace at a time, so the application role cannot list them. The read runs in its own
short transaction that starts with ``SET LOCAL ROLE anum_membership_reader`` (migration
``0013_approvers_and_directory``). That NOLOGIN role can read a few columns, and its
policies admit only the active memberships of the user in ``anum.user_id`` within the
tenant in ``anum.tenant_id`` (and those workspaces' names). Both settings come from the
authenticated identity: the tenant from the token (OIDC) or the development headers,
the user from the token's subject. The transaction is always rolled back.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from .dependencies import AuthenticatedIdentity, authenticated_identity, memory_repository
from .schemas import CallerMembership, TenantContext
from .settings import settings

READER_ROLE = "anum_membership_reader"
_SET_READER_ROLE = text("set local role anum_membership_reader")
# The explicit tenant and user filters repeat what the role's policies already enforce.
_CALLER_MEMBERSHIPS = text(
    """
    select m.workspace_id, m.role, w.name
    from workspace_memberships m
    left join workspaces w on w.tenant_id = m.tenant_id and w.id = m.workspace_id
    where m.tenant_id = :tenant_id and m.user_id = :user_id and m.active
    order by lower(coalesce(w.name, '')), m.workspace_id
    """
)

router = APIRouter(prefix="/api/v1", tags=["workspace-members"])


def list_caller_memberships(context: TenantContext) -> list[CallerMembership]:
    """The caller's active memberships in their tenant, inside the caller's RLS context."""
    if settings.repository_backend == "memory":
        return memory_repository.list_caller_memberships(context)
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        session.execute(_SET_READER_ROLE)
        # No workspace: the directory spans the tenant's workspaces, for this user only.
        db_session.set_tenant_context(session, context.tenant_id, None, user_id=context.user_id)
        rows = session.execute(
            _CALLER_MEMBERSHIPS, {"tenant_id": context.tenant_id, "user_id": context.user_id}
        ).all()
        return [
            CallerMembership(
                tenant_id=context.tenant_id,
                workspace_id=row.workspace_id,
                workspace_name=row.name,
                role=row.role,
            )
            for row in rows
        ]
    finally:
        session.rollback()
        session.close()


@router.get("/me/workspace-memberships", response_model=list[CallerMembership])
async def my_workspace_memberships(
    identity: AuthenticatedIdentity = Depends(authenticated_identity),
) -> list[CallerMembership]:
    """Every workspace of the caller's tenant where the caller has an active membership.

    Open to every authenticated caller: it reveals only the caller's own memberships, so
    no membership in the currently selected workspace is required (someone deactivated
    there can still find the workspaces they may switch to).
    """
    return list_caller_memberships(identity.context)
