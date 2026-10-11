"""ANUM realm roles on an upgraded Keycloak (docs/deploy-oracle.md#organization-administrators).

Keycloak imports the realm file only on its first start, so a realm created before
``org_admin`` existed lacks it and every tenant-wide policy change answers 403.
infra/keycloak/realm_roles.py creates the missing roles and grants ``org_admin`` to a
user someone chose; infra/oracle/keycloak-2fa.sh runs it in the Oracle VM's Keycloak
container and update.sh prints a notice when nobody holds the role. These tests drive the
script against an in-memory stand-in for Keycloak's admin API through kcadm argv. No
network, no Docker.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REALM_ROLES = REPO_ROOT / "infra" / "keycloak" / "realm_roles.py"
REALM = REPO_ROOT / "infra" / "keycloak" / "anum-realm.json"
WRAPPER = REPO_ROOT / "infra" / "oracle" / "keycloak-2fa.sh"
UPDATE = REPO_ROOT / "infra" / "oracle" / "update.sh"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rr = _load("anum_realm_roles", REALM_ROLES)


class FakeKeycloak:
    """Realm roles, users and realm role mappings of Keycloak 26's admin API, via kcadm argv."""

    def __init__(self, roles: tuple[str, ...] = ("owner", "member", "viewer")) -> None:
        self.roles: dict[str, dict[str, Any]] = {
            name: {"id": str(uuid.uuid4()), "name": name, "description": "imported"}
            for name in (*roles, "offline_access", "default-roles-anum")
        }
        self.users = {
            "u1": {
                "id": "u1",
                "username": "shahzeb",
                "attributes": {"tenant_id": ["shahzeb_company"]},
            },
            "u2": {"id": "u2", "username": "no-tenant", "attributes": {}},
            "u3": {"id": "u3", "username": "shahzeb2", "attributes": {"tenant_id": ["other"]}},
        }
        self.mappings: dict[str, list[str]] = {"u1": [self.roles["owner"]["id"]]}
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], input: str | None = None, **_: Any
    ) -> subprocess.CompletedProcess[str]:
        assert argv[:1] == ["kcadm"]
        self.calls.append(argv)
        args = argv[1:]
        verb, path = args[0], args[1]
        assert args[args.index("-r") + 1] == "anum"
        query = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-q")
        body = json.loads(input) if input else None
        try:
            out = self.handle(verb, path, query, body)
        except LookupError as error:
            return subprocess.CompletedProcess(argv, 1, "", f"Resource not found: {error}\n")
        return subprocess.CompletedProcess(
            argv, 0, out if isinstance(out, str) else json.dumps(out), ""
        )

    def _role_by_id(self, role_id: str) -> dict[str, Any]:
        return next(role for role in self.roles.values() if role["id"] == role_id)

    def handle(self, verb: str, path: str, query: dict[str, str], body: Any) -> Any:
        parts = path.split("/")
        if path == "roles" and verb == "get":
            return list(self.roles.values())
        if path == "roles" and verb == "create":
            if body["name"] in self.roles:
                raise LookupError(f"Conflict: role {body['name']} exists")
            self.roles[body["name"]] = {"id": str(uuid.uuid4()), **body}
            return ""
        if parts[0] == "roles" and parts[2:] == ["users"]:
            role = self.roles.get(parts[1])
            if role is None:
                raise LookupError(parts[1])
            brief = query.get("briefRepresentation", "true") == "true"
            return [
                {k: v for k, v in user.items() if not (brief and k == "attributes")}
                for user_id, user in self.users.items()
                if role["id"] in self.mappings.get(user_id, [])
            ]
        if path == "users":
            assert query.get("exact") == "true"
            return [u for u in self.users.values() if query["username"] in u["username"]]
        if parts[0] == "users" and parts[2:] == ["role-mappings", "realm"]:
            if verb == "get":
                return [self._role_by_id(i) for i in self.mappings.get(parts[1], [])]
            for role in body:
                assert self._role_by_id(role["id"])["name"] == role["name"]
                if role["id"] not in self.mappings.setdefault(parts[1], []):
                    self.mappings[parts[1]].append(role["id"])
            return ""
        raise AssertionError(f"unexpected kcadm {verb} {path}")

    def holders(self, name: str) -> list[str]:
        role_id = self.roles[name]["id"]
        return sorted(
            self.users[user_id]["username"]
            for user_id, ids in self.mappings.items()
            if role_id in ids
        )

    def writes(self) -> list[list[str]]:
        return [call for call in self.calls if call[1] != "get"]


@pytest.fixture
def keycloak() -> FakeKeycloak:
    # A realm imported before org_admin existed: the upgrade case.
    return FakeKeycloak()


def kcadm(keycloak: FakeKeycloak) -> Any:
    return rr.Kcadm(["kcadm"], runner=keycloak)


def test_ensure_roles_creates_org_admin_on_an_upgraded_realm_and_grants_nothing(
    keycloak: FakeKeycloak,
) -> None:
    log: list[str] = []
    rr.ensure_roles(kcadm(keycloak), log=log.append)
    assert keycloak.roles["org_admin"]["description"] == rr.REALM_ROLES["org_admin"]
    assert keycloak.holders("org_admin") == []
    assert log == ["created realm role org_admin in realm anum"]
    # Existing roles are left as they are.
    assert keycloak.roles["owner"]["description"] == "imported"


def test_ensure_roles_is_idempotent(keycloak: FakeKeycloak) -> None:
    rr.ensure_roles(kcadm(keycloak), log=lambda _: None)
    keycloak.calls.clear()
    rr.ensure_roles(kcadm(keycloak), log=lambda _: None)
    assert keycloak.writes() == []


def test_grant_org_admin_assigns_the_role_to_the_chosen_user_only(keycloak: FakeKeycloak) -> None:
    log: list[str] = []
    assert rr.grant_org_admin(kcadm(keycloak), "shahzeb", log=log.append) is True
    assert keycloak.holders("org_admin") == ["shahzeb"]
    # Exact username match: shahzeb2 (a substring match in Keycloak's search) is untouched.
    assert keycloak.holders("owner") == ["shahzeb"]
    assert "shahzeb (tenant shahzeb_company) now holds org_admin" in log[-1]
    assert "sign out" in log[-1]


def test_grant_org_admin_twice_changes_nothing(keycloak: FakeKeycloak) -> None:
    rr.grant_org_admin(kcadm(keycloak), "shahzeb", log=lambda _: None)
    keycloak.calls.clear()
    log: list[str] = []
    assert rr.grant_org_admin(kcadm(keycloak), "shahzeb", log=log.append) is False
    assert keycloak.writes() == []
    assert "already holds org_admin" in log[-1]


def test_grant_org_admin_refuses_an_unknown_user_or_one_without_a_tenant(
    keycloak: FakeKeycloak,
) -> None:
    with pytest.raises(rr.KcadmError, match="no user named 'nobody'"):
        rr.grant_org_admin(kcadm(keycloak), "nobody", log=lambda _: None)
    with pytest.raises(rr.KcadmError, match="no tenant_id"):
        rr.grant_org_admin(kcadm(keycloak), "no-tenant", log=lambda _: None)
    with pytest.raises(rr.KcadmError, match="needs a username"):
        rr.grant_org_admin(kcadm(keycloak), "", log=lambda _: None)
    assert keycloak.holders("org_admin") == []


def test_status_explains_a_missing_role_and_lists_holders(keycloak: FakeKeycloak) -> None:
    missing = rr.status(kcadm(keycloak))
    assert "missing" in missing[0] and "grant-org-admin" in missing[0]
    rr.ensure_roles(kcadm(keycloak), log=lambda _: None)
    assert "no user holds it" in "\n".join(rr.status(kcadm(keycloak)))
    rr.grant_org_admin(kcadm(keycloak), "shahzeb", log=lambda _: None)
    assert rr.status(kcadm(keycloak)) == [
        "realm role org_admin: present",
        "  shahzeb (tenant shahzeb_company)",
    ]


def test_main_runs_through_kcadm_with_bodies_on_stdin(
    keycloak: FakeKeycloak, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[list[str], str | None]] = []

    def runner(argv: list[str], input: str | None = None, **kwargs: Any) -> Any:
        seen.append((argv, input))
        stripped = [a for a in argv if a not in ("--config", "/tmp/kcadm.config")]
        return keycloak(stripped, input=input, **kwargs)

    real = rr.Kcadm
    monkeypatch.setattr(
        rr, "Kcadm", lambda command, config="": real(command, config=config, runner=runner)
    )
    code = rr.main(
        ["grant-org-admin", "--username", "shahzeb", "--config", "/tmp/kcadm.config", "--", "kcadm"]
    )
    assert code == 0 and keycloak.holders("org_admin") == ["shahzeb"]
    assert all(argv[argv.index("--config") + 1] == "/tmp/kcadm.config" for argv, _ in seen)
    # JSON bodies travel on stdin (-f -), never as arguments.
    assert all(not any(a.startswith(("{", "[")) for a in argv) for argv, _ in seen)
    assert all("-f" in argv for argv, body in seen if body is not None)
    assert rr.main(["grant-org-admin", "--username", "nobody", "--", "kcadm"]) == 1
    assert "ERROR: no user named 'nobody'" in capsys.readouterr().err


def test_role_descriptions_match_the_realm_file() -> None:
    realm = json.loads(REALM.read_text(encoding="utf-8"))
    imported = {role["name"]: role["description"] for role in realm["roles"]["realm"]}
    assert {name: imported[name] for name in rr.REALM_ROLES} == rr.REALM_ROLES


def test_wrapper_offers_the_role_commands_and_apply_ensures_the_roles() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "../keycloak/realm_roles.py" in text
    assert "grant-org-admin) python3 \"$REALM_ROLES\" grant-org-admin --username" in text
    apply = text[text.index("  apply)\n") : text.index("    ;;", text.index("  apply)\n"))]
    assert 'python3 "$REALM_ROLES" ensure-roles' in apply
    assert "sudo" not in text.replace("no sudo", "")


def test_update_notices_a_missing_org_admin_without_admin_credentials() -> None:
    text = UPDATE.read_text(encoding="utf-8")
    check = text[text.index("org_admin_counts() {") :]
    # Read-only, peer-authenticated inside the postgres container: no password anywhere.
    assert "default_transaction_read_only=on" in check
    assert "-u postgres" in check and "PASSWORD" not in check
    assert "keycloak-2fa.sh grant-org-admin <your ANUM username>" in check
    # It never grants the role itself.
    assert "insert" not in check.lower() and "keycloak-2fa.sh grant-org-admin \"" not in check
