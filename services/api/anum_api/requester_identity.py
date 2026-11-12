"""Who an approved tool call runs as (docs/approvals-and-risk.md#who-the-action-runs-as).

An approver only approves: the call then runs as the person whose run proposed it, with that
person's permissions. The durable workflow already does this (its activity runs in the context
the run was started with); the inline path used to run the call in the approver's context, so an
owner approving a member's request lent the member the owner's role (security audit, November
2026, item 4).

When a run pauses for approval it records ``RunCheckpoint.requested_as``: the requester's user
id, workspace roles, role template limit and organization roles at that moment. When the
approval is decided, :func:`requester_identity` rebuilds that person's context the way a request
of theirs would be authorised now:

* the approver is the requester: the approver's own (just authenticated) context;
* the requester has a stored membership in the workspace: its role and role template win (as in
  ``dependencies.tenant_context``), and an inactive membership means nobody to run as;
* no stored membership: in OIDC mode nobody to run as (they could not make a request either);
  in header mode (``local`` and ``test`` only) the recorded roles.

``None`` means the call must not run; the caller fails the run with :data:`REQUESTER_GONE`.
"""

from __future__ import annotations

from typing import Any

from .schemas import AgentRun, Approval, TenantContext

REQUESTER_GONE = (
    "The person who asked for this action is no longer an active member of this workspace, "
    "so it was not run (an approved action runs as the person who asked, never as the approver)."
)


def requested_as(context: TenantContext) -> dict[str, Any]:
    """The requester as recorded on the run's checkpoint when it pauses for approval."""
    return {
        "user_id": context.user_id,
        "roles": list(context.roles),
        "permission_limit": list(context.permission_limit) if context.permission_limit is not None else None,
        "organization_roles": list(context.organization_roles),
    }


def requester_identity(
    repository: Any, approver: TenantContext, approval: Approval, run: AgentRun | None, *, auth_mode: str
) -> TenantContext | None:
    """The context the approved call runs in: its requester's, or ``None`` when there is none."""
    recorded = run.checkpoint.requested_as if run is not None else None
    requester = approval.requested_by or (str(recorded.get("user_id") or "") if recorded else "")
    if not requester or requester == approver.user_id:
        return approver
    base = TenantContext(
        tenant_id=approver.tenant_id,
        workspace_id=approver.workspace_id,
        user_id=requester,
        roles=[str(role) for role in (recorded or {}).get("roles") or []],
        organization_roles=[str(role) for role in (recorded or {}).get("organization_roles") or []],
        permission_limit=(recorded or {}).get("permission_limit"),
    )
    if recorded is not None and str(recorded.get("user_id") or "") != requester:
        return None  # the checkpoint names someone else: never guess
    membership = repository.get_membership(base)
    if membership is not None:
        if not membership.active:
            return None
        limit = membership.permission_limit
        return base.model_copy(update={
            "roles": [membership.role.lower()],
            "permission_limit": list(limit) if limit is not None else None,
        })
    if auth_mode == "oidc" or recorded is None:
        return None
    return base


def approver_identity(repository: Any, context: TenantContext, approval: Approval) -> TenantContext | None:
    """The person who approved ``approval`` (its ``decided_by``), with their stored workspace role,
    for a tool that records or checks its approver (``agent_tools.approver_of``). The durable worker
    uses it; the inline path has the approver's own request context."""
    if not approval.decided_by:
        return None
    approver = TenantContext(tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=approval.decided_by, roles=[])
    membership = repository.get_membership(approver)
    if membership is None or not membership.active:
        return approver
    return approver.model_copy(update={"roles": [membership.role.lower()], "permission_limit": membership.permission_limit})


__all__ = ["REQUESTER_GONE", "approver_identity", "requested_as", "requester_identity"]
