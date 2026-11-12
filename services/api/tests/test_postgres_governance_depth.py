"""PostgreSQL: governance depth (migration 0166_governance_depth), as the non-owner app role.

Archived policy pack versions can be deleted under tenant RLS; a role template assigned to a
membership is stored on the row and narrows the member's permissions on their next request;
routing target probe results persist, the in-use check refuses the delete, and the scheduled
pass discovers due targets as ``anum_maintenance`` (ids, workspace and time only).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import httpx
import pytest
from conftest import APP_ROLE, FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

import anum_api.onboarding as onboarding
from anum_api import routing_probe
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import WorkspaceMembership
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, roles: str = "owner,org_admin", user: str = "owner_pg") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


A = headers()
B = headers(TENANT_B, WORKSPACE_B)
MEMBER = headers(roles="member", user="member_pg")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, repository_factory, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    for user, role in (("owner_pg", "owner"), ("member_pg", "member")):
        with repository_factory(tenant_context(), commit=True) as repository:
            repository.save_membership(WorkspaceMembership(
                tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user, role=role, created_at=FIXED_NOW, updated_at=FIXED_NOW,
            ))
    yield TestClient(app)


def _rows(app_session: Callable[..., Iterator[Session]], sql: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> list:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        return list(session.execute(text(sql)).all())


POLICY = {"name": "External actions", "description": "d", "rules": [{"action": "integration.*.write", "effect": "require_approval"}]}
TARGET = {"id": "eu-a", "region": "eu-west", "provider": "openai-compatible", "model": "primary", "status": "healthy", "cost_per_1k_tokens": 0.01, "latency_ms": 200}
CONFIG = {"provider": "openai_compatible", "model": "primary", "base_url": "http://localhost:18080/v1", "api_key": "sk-example"}


def _answering(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"model": "primary", "choices": [{"message": {"content": "ANUM_OK"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(onboarding, "_test_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    return seen


def test_archived_pack_versions_are_deleted_in_the_tenant_scope_only(client: TestClient, app_session) -> None:
    first = client.post("/api/v1/policy-packs", headers=A, json=POLICY).json()
    second = client.post("/api/v1/policy-packs", headers=A, json=POLICY).json()
    assert client.delete(f"/api/v1/policy-packs/{second['id']}", headers=A).status_code == 409
    assert client.delete(f"/api/v1/policy-packs/{first['id']}", headers=B).status_code == 404
    assert client.delete(f"/api/v1/policy-packs/{first['id']}", headers=A).status_code == 204
    assert _rows(app_session, "select version, active from policy_packs order by version") == [(2, True)]
    assert _rows(app_session, "select count(*) from audit_records where action = 'policy_pack.deleted'") == [(1,)]
    # Search and paging read the same rows.
    assert [p["version"] for p in client.get("/api/v1/policy-packs?q=external&limit=5", headers=A).json()] == [2]


def test_role_template_on_a_membership_is_stored_and_enforced(client: TestClient, app_session) -> None:
    template = client.post("/api/v1/role-templates", headers=A, json={"name": "Reader", "permissions": ["task:read", "organization:read"]}).json()
    assert client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "p"}).status_code == 201
    assigned = client.put("/api/v1/workspace-members/member_pg/role-template", headers=A, json={"role_template_id": template["id"]})
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["effective_permissions"] == ["organization:read", "task:read"]
    assert _rows(app_session, "select role_template_id, permission_limit from workspace_memberships where user_id = 'member_pg'") == [
        (template["id"], ["organization:read", "task:read"])
    ]
    # The next request of that member is narrowed; reads inside the template still work.
    assert client.get("/api/v1/tasks", headers=MEMBER).status_code == 200
    assert client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "p"}).status_code == 403
    listed = {m["user_id"]: m for m in client.get("/api/v1/workspace-members", headers=A).json()}
    assert listed["member_pg"]["role_template_name"] == "Reader"
    # Another tenant's owner cannot see or change it.
    assert client.put("/api/v1/workspace-members/member_pg/role-template", headers=B, json={"role_template_id": None}).status_code == 404
    assert client.put("/api/v1/workspace-members/member_pg/role-template", headers=A, json={"role_template_id": None}).status_code == 200
    assert client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "p"}).status_code == 201
    assert _rows(app_session, "select count(*) from audit_records where action = 'workspace_member.role_template'") == [(2,)]


def test_probe_results_persist_and_the_in_use_check_guards_the_delete(client: TestClient, app_session, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.put("/api/v1/routing/targets/eu-a", headers=A, json=TARGET).status_code == 200
    assert client.put("/api/v1/model-config", headers=A, json=CONFIG).status_code == 200
    _answering(monkeypatch)
    probed = client.post("/api/v1/routing/targets/eu-a/probe", headers=A)
    assert probed.status_code == 200, probed.text
    assert probed.json()["status"] == "healthy" and probed.json()["probe_workspace_id"] == WORKSPACE_A
    row = _rows(app_session, "select status, enabled, measured_latency_ms, last_error, probe_workspace_id from routing_targets where id = 'eu-a'")[0]
    assert row.status == "healthy" and row.enabled is True and row.measured_latency_ms is not None and row.last_error is None
    assert row.probe_workspace_id == WORKSPACE_A
    # Another tenant sees neither the target nor its probe.
    assert [t["id"] for t in client.get("/api/v1/routing/targets", headers=B).json()] == ["configured-model"]
    assert client.delete("/api/v1/routing/targets/eu-a", headers=B).status_code == 404
    assert client.delete("/api/v1/routing/targets/eu-a", headers=A).status_code == 409
    assert client.put("/api/v1/routing/targets/eu-a", headers=A, json={**TARGET, "enabled": False}).json()["enabled"] is False
    assert _rows(app_session, "select enabled, status from routing_targets where id = 'eu-a'") == [(False, "healthy")]
    assert client.delete("/api/v1/routing/targets/eu-a", headers=A).status_code == 204
    assert _rows(app_session, "select count(*) from routing_targets") == [(0,)]
    actions = [r[0] for r in _rows(app_session, "select action from audit_records where action like 'routing_target.%' order by created_at")]
    assert actions == ["routing_target.saved", "routing_target.probed", "routing_target.saved", "routing_target.deleted"]


def test_maintenance_discovers_due_probes_by_scope_only_and_the_pass_reprobes(
    client: TestClient, app_session, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/api/v1/routing/targets/eu-a", headers=A, json=TARGET)
    client.put("/api/v1/model-config", headers=A, json=CONFIG)
    seen = _answering(monkeypatch)
    assert client.post("/api/v1/routing/targets/eu-a/probe", headers=A).json()["status"] == "healthy"
    assert len(seen) == 1
    assert routing_probe.run_due_routing_probes() == 0
    with database_engine.begin() as connection:
        connection.execute(text("update routing_targets set last_checked_at = last_checked_at - make_interval(secs => :s)"), {"s": settings.routing_probe_interval_seconds + 5})
    # The maintenance role reads the discovery columns (ids, the provider name, times) and
    # nothing else, and cannot write.
    with database_engine.connect() as connection:
        connection.execute(text("set local role anum_maintenance"))
        found = connection.execute(text("select tenant_id, probe_workspace_id, id, provider from routing_targets")).all()
        assert found == [(TENANT_A, WORKSPACE_A, "eu-a", "openai-compatible")]
        assert connection.execute(text("select tenant_id, workspace_id, provider from workspace_model_configs")).all() == [
            (TENANT_A, WORKSPACE_A, "openai_compatible")
        ]
        connection.rollback()
    for secret in ("select model from routing_targets", "select last_error from routing_targets",
                   "select base_url from workspace_model_configs", "select api_key_ciphertext from workspace_model_configs"):
        with database_engine.connect() as connection:
            connection.execute(text("set local role anum_maintenance"))
            with pytest.raises(DBAPIError):
                connection.execute(text(secret))
            connection.rollback()
    with database_engine.connect() as connection:
        connection.execute(text("set local role anum_maintenance"))
        with pytest.raises(DBAPIError):
            connection.execute(text("update routing_targets set enabled = false"))
        connection.rollback()
    assert routing_probe.run_due_routing_probes() == 1
    assert len(seen) == 2
    row = _rows(app_session, "select status, last_checked_at from routing_targets where id = 'eu-a'")[0]
    assert row.status == "healthy" and row.last_checked_at > FIXED_NOW - timedelta(days=1)


def test_the_pass_probes_never_probed_targets_through_a_workspace_configured_for_them(
    client: TestClient, app_session, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0176: never probed targets are discovered too, with a workspace whose model
    configuration is for the target's provider; a target nobody can reach is left alone."""
    client.put("/api/v1/routing/targets/eu-a", headers=A, json=TARGET)
    client.put("/api/v1/routing/targets/an-a", headers=A, json={**TARGET, "id": "an-a", "provider": "anthropic"})
    # Tenant B has a target and no configuration at all.
    client.put("/api/v1/routing/targets/eu-b", headers=B, json={**TARGET, "id": "eu-b"})
    seen = _answering(monkeypatch)
    # Without any configuration nothing is reachable, and the maintenance role sees no configuration.
    assert routing_probe.run_due_routing_probes() == 0 and seen == []
    client.put("/api/v1/model-config", headers=A, json=CONFIG)
    assert routing_probe.run_due_routing_probes() == 1
    assert len(seen) == 1
    rows = _rows(app_session, "select id, status, probe_workspace_id, last_checked_at is not null from routing_targets order by id")
    assert [tuple(r) for r in rows] == [("an-a", "healthy", None, False), ("eu-a", "healthy", WORKSPACE_A, True)]
    assert _rows(app_session, "select probe_workspace_id from routing_targets", TENANT_B, WORKSPACE_B) == [(None,)]
    # A disabled target is never discovered, probed or not.
    with database_engine.begin() as connection:
        connection.execute(text("update routing_targets set enabled = false, last_checked_at = null where id = 'eu-a'"))
    assert routing_probe.run_due_routing_probes() == 0
    with database_engine.connect() as connection:
        connection.execute(text("set local role anum_maintenance"))
        assert connection.execute(text("select id from routing_targets order by id")).all() == [("an-a",), ("eu-b",)]
        connection.rollback()
