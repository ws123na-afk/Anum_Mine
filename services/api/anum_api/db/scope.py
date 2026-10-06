"""Checks shared by the PostgreSQL control-plane stores."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from anum_api.scoped_store import ScopeNotProvisionedError

from .models import Tenant as TenantRecord
from .models import Workspace as WorkspaceRecord


def require_tenant(session: Session, tenant_id: str) -> None:
    """Tenant-level rows need an onboarded tenant (foreign key to ``tenants``)."""
    if session.scalar(select(TenantRecord.id).where(TenantRecord.id == tenant_id)) is None:
        raise ScopeNotProvisionedError(tenant_id)


def require_workspace(session: Session, tenant_id: str, workspace_id: str) -> None:
    """Workspace-level rows need an onboarded workspace; the lookup runs under RLS."""
    found = session.scalar(
        select(WorkspaceRecord.id).where(
            WorkspaceRecord.tenant_id == tenant_id,
            WorkspaceRecord.id == workspace_id,
        )
    )
    if found is None:
        raise ScopeNotProvisionedError(workspace_id)
