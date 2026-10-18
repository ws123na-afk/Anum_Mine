"""Skill signing in PostgreSQL (migration ``0082_skill_signing``), as the non-owner app role.

``skill_publisher_keys`` is under forced tenant RLS: another tenant never sees, revokes or
rotates a tenant's keys, and raw writes into another tenant are refused by the row policy.
Signatures, rotation and revocation survive a restart; a revoked key keeps flagging the
installation and blocks upgrades; rollback goes only to a signed version.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from skill_signing_support import KEYS, private_key, public_key, sign, trust
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.governance import governance_store
from anum_api.main import app
from anum_api.settings import settings
from anum_api.skills_api import skill_store

pytestmark = pytest.mark.database
VERSIONS = "/api/v1/skills/versions"
INSTALLS = "/api/v1/skills/installations"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": "admin_test", "x-user-roles": "owner,org_admin"}


A, A2, B = headers(), headers(workspace_id=WORKSPACE_A2), headers(TENANT_B, WORKSPACE_B)
client = TestClient(app)


def skill(version: str) -> dict[str, Any]:
    return {"skill_id": "acme.notes", "version": version, "name": "Notes", "description": "Writes notes",
            "instructions": "One line per change.", "required_tools": ["anum.respond"], "risk_level": "low"}


def forget() -> None:
    skill_store.clear()
    governance_store.clear()


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    forget()
    yield
    forget()


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def _scoped(connection: Any, tenant_id: str) -> None:
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})


def test_signing_rotation_revocation_and_rollback_persist_across_a_restart(db: None) -> None:
    trust(client, A)
    ok(client.post(VERSIONS, headers=A, json={**skill("1.0.0"), "signature": sign(skill("1.0.0"))}), 201)
    ok(client.post(VERSIONS, headers=A, json=skill("1.5.0")), 201)  # unsigned
    newer = private_key("rotated")
    ok(client.post(f"{KEYS}/test-publisher/rotate", headers=A, json={"new_id": "test-publisher-2", "public_key": public_key(newer)}), 201)
    ok(client.post(VERSIONS, headers=A, json={**skill("2.0.0"), "signature": sign(skill("2.0.0"), newer, "test-publisher-2")}), 201)
    ok(client.post(INSTALLS, headers=A, json={"skill_id": "acme.notes", "version": "2.0.0", "approved_tools": ["anum.respond"]}), 201)

    forget()
    versions = {v["version"]: v["verification"] for v in ok(client.get(VERSIONS, headers=A2))}
    assert versions["1.0.0"]["status"] == "verified" and versions["2.0.0"]["key_id"] == "test-publisher-2"
    assert versions["1.5.0"]["status"] == "unsigned"
    # Rollback refuses the unsigned version and takes the signed one.
    assert client.post(f"{INSTALLS}/acme.notes/rollback", headers=A, json={"version": "1.5.0"}).status_code == 409
    assert ok(client.post(f"{INSTALLS}/acme.notes/rollback", headers=A, json={"version": "1.0.0"}))["version"] == "1.0.0"

    revoked = ok(client.post(f"{KEYS}/test-publisher/revoke", headers=A, json={"reason": "Lost laptop"}))
    assert revoked["affected_versions"] == 1 and revoked["flagged_installations"] == 1
    forget()
    [installed] = ok(client.get(INSTALLS, headers=A))
    assert installed["verification"]["status"] == "revoked" and "Lost laptop" in installed["verification"]["detail"]
    keys = {k["id"]: k["status"] for k in ok(client.get(KEYS, headers=A))["keys"]}
    assert keys == {"test-publisher": "revoked", "test-publisher-2": "active"}
    # The key still signs 2.0.0 (the new key): an upgrade back to it works, 1.0.0 cannot be reinstalled.
    ok(client.post(INSTALLS, headers=A, json={"skill_id": "acme.notes", "version": "2.0.0", "approved_tools": []}), 201)
    assert client.post(INSTALLS, headers=A, json={"skill_id": "acme.notes", "version": "1.0.0", "approved_tools": []}).status_code == 409


def test_publisher_keys_are_isolated_by_tenant_under_rls(db: None, database_engine: Engine) -> None:
    trust(client, A)
    assert ok(client.get(KEYS, headers=B))["keys"] == []
    assert ok(client.get(KEYS, headers=A2))["keys"][0]["id"] == "test-publisher"  # tenant-level: every workspace
    assert client.post(f"{KEYS}/test-publisher/revoke", headers=B, json={"reason": "x"}).status_code == 404
    assert client.post(f"{KEYS}/test-publisher/rotate", headers=B, json={"new_id": "b-key", "public_key": public_key(private_key("b"))}).status_code == 404
    # The same id is free in another tenant.
    trust(client, B)
    assert [k["id"] for k in ok(client.get(KEYS, headers=B))["keys"]] == ["test-publisher"]

    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            assert connection.execute(text("select count(*) from skill_publisher_keys")).scalar_one() == 0
        with connection.begin():
            _scoped(connection, TENANT_B)
            rows = connection.execute(text("select tenant_id from skill_publisher_keys")).scalars().all()
            assert rows == [TENANT_B]
            changed = connection.execute(text(
                "update skill_publisher_keys set status = 'active' where tenant_id = :a"), {"a": TENANT_A}).rowcount
            assert changed == 0
        with pytest.raises(DBAPIError), connection.begin():
            _scoped(connection, TENANT_B)
            connection.execute(text(
                "insert into skill_publisher_keys (tenant_id, id, publisher, public_key, fingerprint, status, created_by) "
                "values (:a, 'planted', 'x', :k, 'f', 'active', 'x')"), {"a": TENANT_A, "k": public_key(private_key("planted"))})
        with pytest.raises(DBAPIError), connection.begin():
            _scoped(connection, TENANT_A)
            # A revoked key must say when; the check constraint refuses a half-revoked row.
            connection.execute(text("update skill_publisher_keys set status = 'revoked' where id = 'test-publisher'"))
    assert ok(client.get(KEYS, headers=A))["keys"][0]["status"] == "active"
