from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .schemas import TenantContext


class Role(StrEnum):
    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


class Permission(StrEnum):
    TASK_READ = "task:read"
    TASK_CREATE = "task:create"
    TASK_RUN = "task:run"
    TASK_CANCEL = "task:cancel"
    APPROVAL_READ = "approval:read"
    APPROVAL_DECIDE = "approval:decide"
    EVENT_READ = "event:read"
    MEMORY_READ = "memory:read"
    MEMORY_CREATE = "memory:create"
    MEMORY_DELETE = "memory:delete"
    FILE_READ = "file:read"
    FILE_CREATE = "file:create"
    FILE_DELETE = "file:delete"


class AuthorizationFailure(StrEnum):
    SCOPE_MISMATCH = "scope_mismatch"
    MEMBERSHIP_MISMATCH = "membership_mismatch"
    MISSING_ROLE = "missing_role"
    PERMISSION_DENIED = "permission_denied"


class AuthorizationError(PermissionError):
    def __init__(self, permission: Permission, reason: AuthorizationFailure) -> None:
        self.permission = permission
        self.reason = reason
        super().__init__(f"{reason.value}: {permission.value}")


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    tenant_id: str
    workspace_id: str
    user_id: str
    role: Role
    active: bool = True


_READ_PERMISSIONS = frozenset(
    {
        Permission.TASK_READ,
        Permission.APPROVAL_READ,
        Permission.EVENT_READ,
        Permission.MEMORY_READ,
        Permission.FILE_READ,
    }
)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OWNER: frozenset(Permission),
        Role.MEMBER: _READ_PERMISSIONS
        | {
            Permission.TASK_CREATE,
            Permission.TASK_RUN,
            Permission.MEMORY_CREATE,
            Permission.MEMORY_DELETE,
            Permission.FILE_CREATE,
            Permission.FILE_DELETE,
        },
        Role.VIEWER: _READ_PERMISSIONS,
    }
)


class AuthorizationPolicy:
    """Evaluate an authenticated context against one workspace membership."""

    def is_allowed(
        self,
        context: TenantContext,
        permission: Permission,
        membership: WorkspaceMembership,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        try:
            self.require(
                context,
                permission,
                membership,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        except AuthorizationError:
            return False
        return True

    def require(
        self,
        context: TenantContext,
        permission: Permission,
        membership: WorkspaceMembership,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        target_tenant_id = tenant_id or context.tenant_id
        target_workspace_id = workspace_id or context.workspace_id

        if (
            target_tenant_id != context.tenant_id
            or target_workspace_id != context.workspace_id
        ):
            raise AuthorizationError(permission, AuthorizationFailure.SCOPE_MISMATCH)

        if (
            not membership.active
            or membership.tenant_id != context.tenant_id
            or membership.workspace_id != context.workspace_id
            or membership.user_id != context.user_id
        ):
            raise AuthorizationError(permission, AuthorizationFailure.MEMBERSHIP_MISMATCH)

        claimed_roles = {role.strip().lower() for role in context.roles}
        if membership.role.value not in claimed_roles:
            raise AuthorizationError(permission, AuthorizationFailure.MISSING_ROLE)

        if permission not in ROLE_PERMISSIONS.get(membership.role, frozenset()):
            raise AuthorizationError(permission, AuthorizationFailure.PERMISSION_DENIED)


policy = AuthorizationPolicy()
