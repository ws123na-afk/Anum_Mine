from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, Header, HTTPException, status

from .repository import AnumRepository, InMemoryRepository
from .authorization import AuthorizationError, Permission, Role, WorkspaceMembership, policy
from .memory import InMemoryMemoryRepository, MemoryRepository
from .schemas import TenantContext
from .settings import settings
from .store import store
from .identity import OidcValidator, local_sessions


memory_repository = InMemoryRepository(store)
memory_note_repository = InMemoryMemoryRepository()
oidc_validator = OidcValidator(settings.keycloak_issuer, settings.oidc_audience)


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
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
) -> TenantContext:
    if authorization and authorization.lower().startswith("bearer anum_local_"):
        if settings.environment != "local":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Local sessions disabled")
        context = local_sessions.resolve(authorization.split(" ", 1)[1])
        if context is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid local session")
        return context
    if settings.auth_mode == "oidc":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
        try:
            claims = await oidc_validator.validate(authorization.split(" ", 1)[1])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc
        return claims.tenant_context()
    if settings.auth_mode != "headers":
        raise RuntimeError(f"Unsupported authentication mode: {settings.auth_mode}")
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
        _require_persisted_membership(memory_repository, context)
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
        repository = SqlAlchemyRepository(session, created_by_user_id=context.user_id)
        _require_persisted_membership(repository, context)
        yield repository
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def memory_repository_context(
    context: TenantContext = Depends(tenant_context),
) -> AsyncIterator[MemoryRepository]:
    if settings.repository_backend == "memory":
        _require_persisted_membership(memory_repository, context)
        yield memory_note_repository
        return

    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.memory_repository import SqlAlchemyMemoryRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        from .db.repository import SqlAlchemyRepository

        _require_persisted_membership(
            SqlAlchemyRepository(session, created_by_user_id=context.user_id),
            context,
        )
        yield SqlAlchemyMemoryRepository(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def provisioning_repository_context(
    context: TenantContext = Depends(tenant_context),
) -> AsyncIterator[AnumRepository]:
    if settings.repository_backend == "memory":
        yield memory_repository
        return
    from .db.repository import SqlAlchemyRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        yield SqlAlchemyRepository(session, created_by_user_id=context.user_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _require_persisted_membership(repository: AnumRepository, context: TenantContext) -> None:
    if settings.auth_mode != "oidc":
        return
    membership = repository.get_membership(context)
    claimed_roles = {role.lower() for role in context.roles}
    if membership is None or not membership.active or membership.role.lower() not in claimed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )
