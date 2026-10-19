"""Turn off what a revoked key signed, in every workspace of the tenant (docs/skills.md#signing).

Runs after the revocation commits, when the tenant's ``disable_flagged`` setting is on (the
default). Installations live per workspace under RLS, so the workspaces that hold an affected
installation are found the maintenance way (``anum_api/maintenance.py``): one discovery query
as ``anum_maintenance``, which may read only scope, id, version and the enabled flag of
enabled installations (migration ``0083_skill_signing``). Each workspace is then changed in
its own short unit of work as the application role inside that workspace's RLS context: the
installation is turned off with the reason, the change is audited there, and the workspace's
owners get a notification. Turning one back on is an explicit owner decision
(``acknowledge_flagged``).
"""

from __future__ import annotations

import logging

from .schemas import TenantContext
from .settings import settings

logger = logging.getLogger("anum_api.skill_flagging")
NOTIFICATION_KIND = "skill_signing"


def _discover(tenant_id: str, versions: set[tuple[str, str]], packages: set[str]) -> set[str]:
    if settings.repository_backend == "memory":
        from .phase5 import store as scale_store
        from .skills_api import skill_store

        found = {workspace for (tenant, workspace, _), item in skill_store.installations.items()
                 if tenant == tenant_id and item.enabled and (item.skill_id, item.version) in versions}
        found |= {workspace for (tenant, workspace, package_id), item in scale_store._installs.items()
                  if tenant == tenant_id and item.enabled and package_id in packages}
        return found
    from sqlalchemy import text

    from .maintenance import default_session_factory, discover

    found: set[str] = set()
    if versions:
        rows = discover(default_session_factory, text(
            "select workspace_id, skill_id, version from skill_installations where tenant_id = :tenant and enabled"
        ), {"tenant": tenant_id})
        found |= {row.workspace_id for row in rows if (row.skill_id, row.version) in versions}
    if packages:
        rows = discover(default_session_factory, text(
            "select workspace_id, package_id from marketplace_installs where tenant_id = :tenant and enabled"
        ), {"tenant": tenant_id})
        found |= {row.workspace_id for row in rows if row.package_id in packages}
    return found


def _disable_in(context: TenantContext, key_id: str, reason: str, versions: set[tuple[str, str]], packages: set[str]) -> list[str]:
    from . import phase5
    from .skills_api import _audit, open_skill_store

    why = f"Turned off automatically: the signing key {key_id} was revoked ({reason})."[:300]
    names: list[str] = []
    with open_skill_store(context) as store:
        for installation in store.list_installations(context):
            if installation.enabled and (installation.skill_id, installation.version) in versions:
                store.save_installation(installation.model_copy(update={"enabled": False, "disabled_reason": why}))
                _audit(store, context, "skill.installation_disabled", installation.skill_id, {
                    "version": installation.version, "key_id": key_id, "reason": reason, "automatic": True,
                }, outcome="flagged")
                names.append(f"skill {installation.skill_id} {installation.version}")
    if packages:
        with phase5.open_scale_store(context) as scale:
            for install in scale.installs(context):
                if install.enabled and install.package_id in packages:
                    scale.set_install(context, install.model_copy(update={"enabled": False, "disabled_reason": why}))
                    phase5._audit(scale, context, "marketplace.package_disabled", install.package_id, {
                        "version": install.version, "key_id": key_id, "reason": reason, "automatic": True,
                    }, outcome="flagged")
                    names.append(f"integration {install.package_id} {install.version}")
    return names


def _notify_owners(context: TenantContext, key_id: str, names: list[str]) -> int:
    from .team_work import open_team_unit

    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    body = (f"The signing key {key_id} was revoked, so ANUM turned off {shown} in this workspace. "
            "Agents can no longer use them. Upgrade or roll back to a version signed by a trusted key, "
            "or turn one back on in Skills if you accept the risk.")
    with open_team_unit(context) as unit:
        owners = [m.user_id for m in unit.repository.list_memberships(context) if m.role == "owner" and m.active]
        for owner in owners:
            unit.notify(owner, NOTIFICATION_KIND, "Skills turned off: signing key revoked", body, None)
    return len(owners)


def disable_signed_installs(
    context: TenantContext, key_id: str, reason: str, versions: set[tuple[str, str]], packages: set[str],
) -> int:
    """Turn off every enabled installation of ``versions`` and ``packages`` in the tenant; returns how many."""
    total = 0
    for workspace_id in sorted(_discover(context.tenant_id, versions, packages)):
        scoped = context.model_copy(update={"workspace_id": workspace_id})
        names = _disable_in(scoped, key_id, reason, versions, packages)
        if names:
            try:
                _notify_owners(scoped, key_id, names)
            except Exception:  # noqa: BLE001 - the installs are off and audited; a missed notice must not undo that
                logger.exception("Could not notify the owners of %s about disabled installations", workspace_id)
        total += len(names)
    return total
