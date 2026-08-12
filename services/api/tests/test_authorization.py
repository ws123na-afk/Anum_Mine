import pytest

from anum_api.authorization import (
    AuthorizationError,
    AuthorizationFailure,
    AuthorizationPolicy,
    Permission,
    Role,
    WorkspaceMembership,
)
from anum_api.schemas import TenantContext


def make_context(*roles: str) -> TenantContext:
    return TenantContext(
        tenant_id="tenant_a",
        workspace_id="workspace_a",
        user_id="user_a",
        roles=list(roles),
    )


def make_membership(role: Role, **overrides: object) -> WorkspaceMembership:
    values = {
        "tenant_id": "tenant_a",
        "workspace_id": "workspace_a",
        "user_id": "user_a",
        "role": role,
        "active": True,
    }
    values.update(overrides)
    return WorkspaceMembership(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (Role.OWNER, Permission.APPROVAL_DECIDE),
        (Role.OWNER, Permission.TASK_CANCEL),
        (Role.MEMBER, Permission.TASK_CREATE),
        (Role.MEMBER, Permission.TASK_RUN),
        (Role.VIEWER, Permission.TASK_READ),
        (Role.VIEWER, Permission.EVENT_READ),
    ],
)
def test_role_permissions_allow_expected_actions(role: Role, permission: Permission) -> None:
    authorization = AuthorizationPolicy()

    assert authorization.is_allowed(
        make_context(role.value), permission, make_membership(role)
    )


@pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
@pytest.mark.parametrize(
    "permission", [Permission.APPROVAL_DECIDE, Permission.TASK_CANCEL]
)
def test_dangerous_actions_require_owner(role: Role, permission: Permission) -> None:
    authorization = AuthorizationPolicy()

    with pytest.raises(AuthorizationError) as error:
        authorization.require(make_context(role.value), permission, make_membership(role))

    assert error.value.reason is AuthorizationFailure.PERMISSION_DENIED
    assert error.value.permission is permission


@pytest.mark.parametrize(
    "target",
    [
        {"tenant_id": "tenant_b"},
        {"workspace_id": "workspace_b"},
    ],
)
def test_target_scope_must_match_context(target: dict[str, str]) -> None:
    with pytest.raises(AuthorizationError) as error:
        AuthorizationPolicy().require(
            make_context("owner"),
            Permission.TASK_READ,
            make_membership(Role.OWNER),
            **target,
        )

    assert error.value.reason is AuthorizationFailure.SCOPE_MISMATCH


@pytest.mark.parametrize(
    "membership_change",
    [
        {"tenant_id": "tenant_b"},
        {"workspace_id": "workspace_b"},
        {"user_id": "user_b"},
        {"active": False},
    ],
)
def test_membership_must_match_context(membership_change: dict[str, object]) -> None:
    with pytest.raises(AuthorizationError) as error:
        AuthorizationPolicy().require(
            make_context("owner"),
            Permission.TASK_READ,
            make_membership(Role.OWNER, **membership_change),
        )

    assert error.value.reason is AuthorizationFailure.MEMBERSHIP_MISMATCH


def test_missing_claimed_role_is_denied() -> None:
    with pytest.raises(AuthorizationError) as error:
        AuthorizationPolicy().require(
            make_context(), Permission.TASK_READ, make_membership(Role.VIEWER)
        )

    assert error.value.reason is AuthorizationFailure.MISSING_ROLE


def test_different_claimed_role_cannot_elevate_membership() -> None:
    with pytest.raises(AuthorizationError) as error:
        AuthorizationPolicy().require(
            make_context("owner"),
            Permission.APPROVAL_DECIDE,
            make_membership(Role.MEMBER),
        )

    assert error.value.reason is AuthorizationFailure.MISSING_ROLE


def test_unknown_role_claim_does_not_grant_permissions() -> None:
    assert not AuthorizationPolicy().is_allowed(
        make_context("administrator"),
        Permission.TASK_READ,
        make_membership(Role.VIEWER),
    )
