from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, status

from .repository import AnumRepository, InMemoryRepository
from .schemas import TenantContext
from .settings import settings
from .store import store


memory_repository = InMemoryRepository(store)


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


async def repository_context(
    context: TenantContext = Depends(tenant_context),
) -> AsyncIterator[AnumRepository]:
    if settings.repository_backend == "memory":
        yield memory_repository
        return

    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.repository import SqlAlchemyRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        session.info["user_id"] = context.user_id
        yield SqlAlchemyRepository(session, created_by_user_id=context.user_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
