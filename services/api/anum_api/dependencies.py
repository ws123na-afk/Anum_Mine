from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import Depends, Header, HTTPException, status

from .repository import AnumRepository, InMemoryRepository
from .authorization import AuthorizationError, Permission, Role, WorkspaceMembership, policy
from .idempotency import InMemoryIdempotencyRepository, InvalidIdempotencyKey, validate_idempotency_key
from .memory import InMemoryMemoryRepository, MemoryRepository
from .oidc_auth import get_jwks_client, resolve_tenant_context_from_bearer
from .schemas import TenantContext
from .settings import settings
from .store import store

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


memory_repository = InMemoryRepository(store)
memory_note_repository = InMemoryMemoryRepository()
idempotency_repository = InMemoryIdempotencyRepository()


def require_permission(context: TenantContext, permission: Permission) -> None:
    claimed_roles = {role.strip().lower() for role in context.roles}
    role = next(
        (candidate for candidate in Role if candidate.value in claimed_roles),
        None,
    )
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace permission denied",
        )
    membership = WorkspaceMembership(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        role=role,
    )
    try:
        policy.require(context, permission, membership)
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace permission denied",
        ) from exc


async def tenant_context(
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
    authorization: str | None = Header(default=None),
) -> TenantContext:
    if settings.auth_mode == "oidc":
        # OIDC opt-in: the stub tenant/workspace/user/role headers above are
        # ignored entirely — trust only a validated Bearer token.
        return await resolve_tenant_context_from_bearer(authorization, get_jwks_client())

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


async def db_session_context(
    context: TenantContext = Depends(tenant_context),
) -> AsyncIterator["Session | None"]:
    """Yield one request-scoped session shared by every repository dependency.

    FastAPI caches dependency results per request, so every endpoint
    parameter depending on this function (directly or via repository_context /
    memory_repository_context) reuses the same session and transaction
    instead of opening a separate one each.
    """

    if settings.repository_backend == "memory":
        yield None
        return

    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        session.info["user_id"] = context.user_id
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def repository_context(
    context: TenantContext = Depends(tenant_context),
    session: "Session | None" = Depends(db_session_context),
) -> AnumRepository:
    if settings.repository_backend == "memory":
        return memory_repository

    from .db.repository import SqlAlchemyRepository

    return SqlAlchemyRepository(session, created_by_user_id=context.user_id)


async def memory_repository_context(
    session: "Session | None" = Depends(db_session_context),
) -> MemoryRepository:
    if settings.repository_backend == "memory":
        return memory_note_repository

    from .db.memory_repository import SqlAlchemyMemoryRepository

    return SqlAlchemyMemoryRepository(session)


async def idempotency_key_header(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    if idempotency_key is None:
        return None
    try:
        return validate_idempotency_key(idempotency_key)
    except InvalidIdempotencyKey as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
