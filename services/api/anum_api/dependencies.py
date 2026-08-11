from fastapi import Header, HTTPException, status

from .schemas import TenantContext


async def tenant_context(
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
) -> TenantContext:
    if not x_tenant_id or not x_workspace_id or not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing ANUM tenant context headers",
        )

    roles = [role.strip() for role in (x_user_roles or "member").split(",") if role.strip()]
    return TenantContext(
        tenant_id=x_tenant_id,
        workspace_id=x_workspace_id,
        user_id=x_user_id,
        roles=roles,
    )
