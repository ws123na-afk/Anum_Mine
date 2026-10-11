"""ANUM realm roles on a running Keycloak, and who holds `org_admin` (docs/identity.md#organization-administrators).

    python3 realm_roles.py ensure-roles [--config <kcadm config>] -- <kcadm command...>
    python3 realm_roles.py grant-org-admin --username <name> [--config ...] -- <kcadm...>
    python3 realm_roles.py status [--config <kcadm config>] -- <kcadm command...>

Keycloak imports the realm file (infra/keycloak/anum-realm.json) only on its first start,
so a realm created before a role was added to that file never gets it. On such an upgrade
nobody holds `org_admin`, and every change to tenant-wide policy (policy packs, role
templates, approval rules, memory governance) is refused with 403, owners included.

* ``ensure-roles`` creates the ANUM realm roles that are missing (``owner``, ``member``,
  ``viewer``, ``org_admin``) with the realm file's descriptions. Existing roles are not
  touched, and no user is given anything.
* ``grant-org-admin`` ensures the roles, then assigns the realm role ``org_admin`` to one
  existing user of the realm. It is never granted implicitly: an organization
  administrator may change policy for every workspace of the user's ``tenant_id``, so the
  person running this chooses who. Assigning it again changes nothing.
* ``status`` says whether ``org_admin`` exists and lists the users who hold it directly,
  with their ``tenant_id``.

`infra/oracle/keycloak-2fa.sh` logs kcadm in and runs this (its ``apply`` runs
``ensure-roles`` too). Removing the role from a user is done in the admin console
(realm anum -> Users -> the user -> Role mapping -> Unassign). Standard library only.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from two_factor import REALM, Kcadm, KcadmError

ORG_ADMIN = "org_admin"

# Kept equal to infra/keycloak/anum-realm.json by services/api/tests/test_keycloak_realm_roles.py.
REALM_ROLES: dict[str, str] = {
    "owner": "ANUM workspace owner (authoritative role is the ANUM workspace membership).",
    "member": "ANUM workspace member (authoritative role is the ANUM workspace membership).",
    "viewer": "ANUM read-only viewer (authoritative role is the ANUM workspace membership).",
    ORG_ADMIN: (
        "ANUM organization administrator for the user's tenant_id: may change tenant-wide "
        "policy (policy packs, role templates, approval rules, memory governance). "
        "Authoritative; grant it only to the tenant's administrators."
    ),
}


def realm_roles(kcadm: Kcadm) -> dict[str, dict[str, Any]]:
    return {role["name"]: role for role in kcadm.get("roles") or []}


def ensure_roles(kcadm: Kcadm, log: Callable[[str], None] = print) -> dict[str, dict[str, Any]]:
    """Create the missing ANUM realm roles; returns every realm role by name."""
    roles = realm_roles(kcadm)
    missing = [name for name in REALM_ROLES if name not in roles]
    for name in missing:
        kcadm.add("roles", {"name": name, "description": REALM_ROLES[name]})
        log(f"created realm role {name} in realm {REALM}")
    if missing:
        roles = realm_roles(kcadm)
    else:
        log(f"realm roles {', '.join(REALM_ROLES)} exist in realm {REALM}")
    return roles


def _user(kcadm: Kcadm, username: str) -> dict[str, Any]:
    users = [
        user
        for user in kcadm.get("users", username=username, exact="true") or []
        if user.get("username") == username
    ]
    if len(users) != 1:
        raise KcadmError(
            f"no user named {username!r} in realm {REALM}; check the spelling in the admin "
            "console (realm anum -> Users)"
        )
    return users[0]


def _tenant(user: dict[str, Any]) -> str:
    values = (user.get("attributes") or {}).get("tenant_id") or []
    return values[0] if values else ""


def grant_org_admin(kcadm: Kcadm, username: str, log: Callable[[str], None] = print) -> bool:
    """Assign org_admin to ``username``; True when it was newly assigned."""
    if not username:
        raise KcadmError("grant-org-admin needs a username")
    roles = ensure_roles(kcadm, log)
    user = _user(kcadm, username)
    tenant = _tenant(user)
    if not tenant:
        raise KcadmError(
            f"{username} has no tenant_id attribute, so org_admin would apply to no "
            "organization; set tenant_id first (realm anum -> Users -> "
            f"{username} -> Attributes)"
        )
    path = f"users/{user['id']}/role-mappings/realm"
    if any(role.get("name") == ORG_ADMIN for role in kcadm.get(path) or []):
        log(f"{username} (tenant {tenant}) already holds {ORG_ADMIN}; nothing changed")
        return False
    role = roles[ORG_ADMIN]
    kcadm.add(path, [{"id": role["id"], "name": role["name"]}])
    log(
        f"{username} (tenant {tenant}) now holds {ORG_ADMIN}: they may change policy packs, "
        "role templates, approval rules and memory governance for that organization. "
        "It takes effect at their next sign-in (sign out of ANUM and in again)."
    )
    return True


def status(kcadm: Kcadm) -> list[str]:
    if ORG_ADMIN not in realm_roles(kcadm):
        return [
            (
                f"realm role {ORG_ADMIN}: missing (realm imported before it existed). Nobody "
                "can change tenant-wide policy until it is created and granted: run "
                "ensure-roles, then grant-org-admin <username>."
            )
        ]
    # briefRepresentation=false: the listing then carries attributes (tenant_id).
    holders = kcadm.get(f"roles/{ORG_ADMIN}/users", briefRepresentation="false") or []
    lines = [f"realm role {ORG_ADMIN}: present"]
    if not holders:
        lines.append(
            "  no user holds it directly, so nobody can change tenant-wide policy unless a "
            "group grants it: run grant-org-admin <username>"
        )
    for user in sorted(holders, key=lambda u: u.get("username", "")):
        tenant = _tenant(user)
        lines.append(f"  {user.get('username')} (tenant {tenant or 'none: set tenant_id'})")
    return lines


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("action", choices=["ensure-roles", "grant-org-admin", "status"])
    parser.add_argument("--username", default="", help="grant-org-admin: the anum realm user")
    parser.add_argument("--config", default="", help="kcadm's --config file, if not the default")
    parser.add_argument("kcadm", nargs="+", help="the logged-in kcadm command, after --")
    args = parser.parse_args(argv)
    kcadm = Kcadm(args.kcadm, config=args.config)
    try:
        if args.action == "ensure-roles":
            ensure_roles(kcadm)
        elif args.action == "grant-org-admin":
            grant_org_admin(kcadm, args.username)
        else:
            print("\n".join(status(kcadm)))
    except KcadmError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
