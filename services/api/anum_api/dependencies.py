from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, status

from .repository import AnumRepository, InMemoryRepository
from .event_bus import EventCollectingRepository, build_event_runtime
from .authorization import AuthorizationError, Permission, Role, WorkspaceMembership, policy
from .memory import InMemoryMemoryRepository, MemoryRepository
from .schemas import TenantContext
from .schemas import WorkspaceMembership as WorkspaceMembershipRecord
from .settings import settings
from .store import store
from .identity import (
    JwksUnavailableError,
    OidcClaims,
    OidcValidator,
    TokenValidationError,
    is_local_environment,
    is_valid_scope_id,
    local_sessions,
    local_sessions_allowed,
)


memory_repository = InMemoryRepository(store)
memory_note_repository = InMemoryMemoryRepository()
_oidc_validator: OidcValidator | None = None
event_runtime = build_event_runtime(settings)


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


def get_oidc_validator() -> OidcValidator:
    """Return the process-wide validator, built lazily so the JWKS cache survives requests."""
    global _oidc_validator
    if _oidc_validator is None:
        _oidc_validator = OidcValidator(
            settings.keycloak_issuer,
            settings.oidc_audience,
            jwks_url=settings.oidc_jwks_url,
            leeway_seconds=settings.oidc_leeway_seconds,
            cache_seconds=settings.oidc_jwks_cache_seconds,
            min_refresh_seconds=settings.oidc_jwks_min_refresh_seconds,
        )
    return _oidc_validator


def set_oidc_validator(validator: OidcValidator | None) -> None:
    """Replace (or reset with None) the process-wide validator; used by tests and reconfiguration."""
    global _oidc_validator
    _oidc_validator = validator


_BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="anum"'}


def _unauthorized(detail: str, *, invalid_token: bool = False) -> HTTPException:
    challenge = (
        f'Bearer realm="anum", error="invalid_token", error_description="{detail}"'
        if invalid_token
        else _BEARER_CHALLENGE["WWW-Authenticate"]
    )
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": challenge},
    )


async def _oidc_identity(
    authorization: str | None,
    x_tenant_id: str | None,
    x_workspace_id: str | None,
) -> tuple[OidcClaims, str]:
    """Validate the bearer token and resolve the tenant (from the token) and workspace.

    The tenant always comes from the IdP-asserted `tenant_id` claim; an `x-tenant-id` header,
    when sent, must agree with it. The workspace is the `x-workspace-id` header when present,
    otherwise the token's optional default `workspace_id` claim. Membership is checked by the
    caller.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    if token.startswith("anum_local_"):
        raise _unauthorized("Local sessions are disabled when ANUM_AUTH_MODE=oidc", invalid_token=True)
    try:
        claims = await get_oidc_validator().validate(token)
    except JwksUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider is unavailable",
        ) from exc
    except TokenValidationError as exc:
        raise _unauthorized(exc.reason, invalid_token=True) from exc
    if x_tenant_id and x_tenant_id != claims.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="x-tenant-id does not match the authenticated tenant",
        )
    workspace_id = x_workspace_id or claims.workspace_id
    if not workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a workspace with the x-workspace-id header",
        )
    if not is_valid_scope_id(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="x-workspace-id is not a valid workspace identifier",
        )
    return claims, workspace_id


def lookup_membership(context: TenantContext) -> WorkspaceMembershipRecord | None:
    """Look the caller's membership up through the repository, inside the tenant's RLS scope."""
    if settings.repository_backend == "memory":
        return memory_repository.get_membership(context)
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.repository import SqlAlchemyRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        return SqlAlchemyRepository(session, created_by_user_id=context.user_id).get_membership(context)
    finally:
        session.rollback()
        session.close()


def list_events_for_stream(context: TenantContext) -> list:
    """Read the caller's events in a fresh, tenant-scoped session.

    Streaming responses outlive their request dependencies (the repository
    session is closed before the body is sent), so every poll opens its own
    session with the tenant and workspace set for RLS.
    """
    if settings.repository_backend == "memory":
        return memory_repository.list_events(context)
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.repository import SqlAlchemyRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        return SqlAlchemyRepository(session, created_by_user_id=context.user_id).list_events(context)
    finally:
        session.rollback()
        session.close()


def _membership_context(identity: TenantContext, membership: WorkspaceMembershipRecord) -> TenantContext:
    """The persisted membership role is authoritative for workspace authorization."""
    return identity.model_copy(update={"roles": [membership.role.lower()]})


def _local_session_context(authorization: str) -> TenantContext:
    if not local_sessions_allowed(settings.environment, settings.auth_mode):
        raise _unauthorized("Local sessions are disabled", invalid_token=True)
    context = local_sessions.resolve(authorization.split(" ", 1)[1])
    if context is None:
        raise _unauthorized("Invalid local session", invalid_token=True)
    return context


async def tenant_context(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
) -> TenantContext:
    if settings.auth_mode == "oidc":
        claims, workspace_id = await _oidc_identity(authorization, x_tenant_id, x_workspace_id)
        identity = claims.tenant_context(workspace_id)
        membership = lookup_membership(identity)
        if membership is None or not membership.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active workspace membership required",
            )
        return _membership_context(identity, membership)
    return _header_context(authorization, x_tenant_id, x_workspace_id, x_user_id, x_user_roles)


async def provisioning_tenant_context(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
) -> TenantContext:
    """Context for bootstrap routes (tenant, workspace, membership, onboarding).

    In OIDC mode a caller without a membership yet may bootstrap with the ANUM realm roles in
    the token; once a membership exists its persisted role wins.
    """
    if settings.auth_mode == "oidc":
        claims, workspace_id = await _oidc_identity(authorization, x_tenant_id, x_workspace_id)
        identity = claims.tenant_context(workspace_id)
        membership = lookup_membership(identity)
        if membership is None:
            return identity
        if not membership.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active workspace membership required",
            )
        return _membership_context(identity, membership)
    return _header_context(authorization, x_tenant_id, x_workspace_id, x_user_id, x_user_roles)


def _header_context(
    authorization: str | None,
    x_tenant_id: str | None,
    x_workspace_id: str | None,
    x_user_id: str | None,
    x_user_roles: str | None,
) -> TenantContext:
    if settings.auth_mode != "headers":
        raise RuntimeError(f"Unsupported authentication mode: {settings.auth_mode}")
    if not is_local_environment(settings.environment):
        # validate_auth_configuration refuses this at startup; never trust headers outside local.
        raise RuntimeError("Header authentication is only allowed in local/test environments")
    if authorization and authorization.lower().startswith("bearer anum_local_"):
        return _local_session_context(authorization)
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
        collecting = EventCollectingRepository(memory_repository)
        try:
            yield collecting
        finally:
            # The in-memory store has no rollback: whatever was recorded is visible.
            event_runtime.after_commit(collecting.recorded_events)
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
        collecting = EventCollectingRepository(repository)
        yield collecting
        session.commit()
        # Publish only what the database actually committed (outbox semantics).
        event_runtime.after_commit(collecting.recorded_events)
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
    context: TenantContext = Depends(provisioning_tenant_context),
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
