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
    # Edit or archive any task in the workspace (owners). A task's creator may edit and
    # archive their own task with ``task:create`` alone (docs/tasks.md#permissions).
    TASK_MANAGE = "task:manage"
    APPROVAL_READ = "approval:read"
    APPROVAL_DECIDE = "approval:decide"
    EVENT_READ = "event:read"
    MEMORY_READ = "memory:read"
    MEMORY_CREATE = "memory:create"
    MEMORY_DELETE = "memory:delete"
    # Workspace files (docs/files.md): read (list, preview, download), upload (and rename
    # or delete one's own uploads, re-index), manage (rename or delete anyone's).
    FILE_READ = "file:read"
    FILE_UPLOAD = "file:upload"
    FILE_MANAGE = "file:manage"
    INTEGRATION_READ = "integration:read"
    TENANT_CREATE = "tenant:create"
    WORKSPACE_CREATE = "workspace:create"
    MEMBERSHIP_MANAGE = "membership:manage"
    MARKETPLACE_READ = "marketplace:read"
    MARKETPLACE_MANAGE = "marketplace:manage"
    ROUTING_READ = "routing:read"
    ROUTING_MANAGE = "routing:manage"
    OPERATIONS_READ = "operations:read"
    ORGANIZATION_READ = "organization:read"
    ORGANIZATION_MANAGE = "organization:manage"
    POLICY_READ = "policy:read"
    POLICY_MANAGE = "policy:manage"
    AUDIT_EXPORT = "audit:export"
    # The in-app audit log (docs/governance-and-scale.md#audit-log). Owners hold it through
    # their role; organization administrators read any workspace they are a member of.
    AUDIT_READ = "audit:read"
    # Rename, archive and restore the request's workspace (docs/identity.md#workspace-lifecycle).
    WORKSPACE_MANAGE = "workspace:manage"
    GOVERNANCE_MANAGE = "governance:manage"
    AUTOMATION_READ = "automation:read"
    AUTOMATION_MANAGE = "automation:manage"
    # Tenant-wide policy (policy packs, role templates, organization approval rules, memory
    # governance). Granted only by the organization role below, never by a workspace role:
    # an owner of one workspace must not change policy that binds every workspace.
    ORG_POLICY_MANAGE = "organization_policy:manage"
    # Developer mode (docs/developer-mode.md): workspace owners only (not
    # granted to members or viewers below).
    DEVELOPER_MANAGE = "developer:manage"
    # WhatsApp inbox (docs/whatsapp.md): customer conversations are not for viewers.
    # Members read and ask to send (sends still need approval); owners connect the number.
    WHATSAPP_READ = "whatsapp:read"
    WHATSAPP_REPLY = "whatsapp:reply"
    WHATSAPP_MANAGE = "whatsapp:manage"
    # Email (docs/email.md): reading the unified inbox, acting on messages (mark read,
    # archive, draft, ask to send) and connecting or disconnecting mailboxes (owners).
    # Viewers get none of them: mail is not part of the read-only workspace view.
    EMAIL_READ = "email:read"
    EMAIL_ACT = "email:act"
    EMAIL_MANAGE = "email:manage"
    # Commerce (docs/commerce.md#who-can-do-what): everyone reads sales, orders and stock;
    # members also edit the catalogue, sync and propose price or stock changes (which still
    # need approval); only owners connect channels and see buyer details.
    COMMERCE_READ = "commerce:read"
    COMMERCE_OPERATE = "commerce:operate"
    COMMERCE_MANAGE = "commerce:manage"
    COMMERCE_PII = "commerce:pii"
    # Tyre planning (docs/tyre-planning.md): everyone reads the dashboard, forecasts,
    # plans, alerts and reports; members and owners also sync, generate and schedule
    # reports and acknowledge alerts; only owners connect the data source and change the
    # mapping and assumptions.
    TYRE_READ = "tyre:read"
    TYRE_OPERATE = "tyre:operate"
    TYRE_MANAGE = "tyre:manage"
    # Calls & Meetings (docs/meetings.md): every member reads; owners and members import
    # and edit their own meetings; only owners change or delete anyone's.
    MEETING_READ = "meeting:read"
    MEETING_CREATE = "meeting:create"
    MEETING_MANAGE = "meeting:manage"
    # Team work (docs/team-work.md#permissions): every member reads the workspace's work
    # items; members and owners create, comment and edit what they created or are assigned;
    # owners edit and delete any item, edit anyone's profile and manage assistants.
    WORK_READ = "work:read"
    WORK_MANAGE = "work:manage"
    WORK_ADMIN = "work:admin"
    ASSISTANT_MANAGE = "assistant:manage"
    # Platform operator (docs/customer-organisations.md): onboards customer organisations
    # and reads their counts and health. Granted only by the platform_operator realm role.
    PLATFORM_OPERATE = "platform:operate"


class OrganizationRole(StrEnum):
    """Tenant-level roles. They come from the identity provider (Keycloak realm role in
    ``oidc`` mode), never from a workspace membership, and apply to the token's tenant only."""

    ADMIN = "org_admin"
    # The person running this ANUM deployment for customer companies. Like org_admin it
    # comes from the identity provider only and applies to the token's home tenant.
    PLATFORM_OPERATOR = "platform_operator"


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
        Permission.INTEGRATION_READ,
        Permission.ORGANIZATION_READ,
        Permission.POLICY_READ,
        Permission.MARKETPLACE_READ,
        Permission.ROUTING_READ,
        Permission.OPERATIONS_READ,
        Permission.AUTOMATION_READ,
        Permission.COMMERCE_READ,
        Permission.TYRE_READ,
        Permission.MEETING_READ,
        Permission.WORK_READ,
    }
)

ORGANIZATION_PERMISSIONS = frozenset({Permission.ORG_POLICY_MANAGE, Permission.PLATFORM_OPERATE})

ORGANIZATION_ROLE_PERMISSIONS: Mapping[OrganizationRole, frozenset[Permission]] = MappingProxyType(
    {
        OrganizationRole.ADMIN: frozenset({Permission.ORG_POLICY_MANAGE}),
        OrganizationRole.PLATFORM_OPERATOR: frozenset({Permission.PLATFORM_OPERATE}),
    }
)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OWNER: frozenset(Permission) - ORGANIZATION_PERMISSIONS,
        Role.MEMBER: _READ_PERMISSIONS
        | {
            Permission.TASK_CREATE,
            Permission.TASK_RUN,
            Permission.MEMORY_CREATE,
            Permission.MEMORY_DELETE,
            Permission.FILE_UPLOAD,
            Permission.AUTOMATION_MANAGE,
            Permission.WHATSAPP_READ,
            Permission.WHATSAPP_REPLY,
            Permission.EMAIL_READ,
            Permission.EMAIL_ACT,
            Permission.COMMERCE_OPERATE,
            Permission.TYRE_OPERATE,
            Permission.MEETING_CREATE,
            Permission.WORK_MANAGE,
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

    def require_organization(self, context: TenantContext, permission: Permission) -> None:
        """Tenant-wide permissions: an organization role of the caller's own tenant.

        The caller must still hold an active membership in the request's workspace (the
        request dependencies enforce that); the workspace role itself grants nothing here.
        """
        claimed = {role.strip().lower() for role in context.organization_roles}
        granted = frozenset().union(
            *(
                ORGANIZATION_ROLE_PERMISSIONS[role]
                for role in OrganizationRole
                if role.value in claimed
            )
        )
        if not claimed & {role.value for role in OrganizationRole}:
            raise AuthorizationError(permission, AuthorizationFailure.MISSING_ROLE)
        if permission not in granted:
            raise AuthorizationError(permission, AuthorizationFailure.PERMISSION_DENIED)


policy = AuthorizationPolicy()
