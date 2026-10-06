"""PostgreSQL persistence and RLS for the control-plane stores (migration 0008).

Every test runs the API's stores as the non-owner ``anum_test_app`` role. "Restart" means
a fresh store instance and a fresh session: nothing is read back from process memory,
and the in-memory stores are cleared first to prove it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import (
    APP_ROLE,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    tenant_context,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import files as files_module
from anum_api import onboarding, phase5
from anum_api.db import session as db_session
from anum_api.files import InMemoryObjectStorage, file_store, open_file_metadata_store
from anum_api.governance import governance_store, open_governance_store
from anum_api.integrations import default_integration_registry
from anum_api.main import app
from anum_api.onboarding import NotificationPreferences, open_notification_preference_store
from anum_api.phase5 import open_scale_store
from anum_api.settings import settings
from anum_api.skills_api import open_skill_store, skill_store

pytestmark = pytest.mark.database

CONTROL_PLANE_TABLES = (
    "skill_versions",
    "skill_installations",
    "policy_packs",
    "role_templates",
    "approval_rules",
    "memory_governance",
    "marketplace_packages",
    "marketplace_installs",
    "routing_targets",
    "integration_configurations",
    "workspace_files",
    "notification_preferences",
)


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "user_test") -> dict[str, str]:
    return {
        "x-tenant-id": tenant_id,
        "x-workspace-id": workspace_id,
        "x-user-id": user_id,
        "x-user-roles": "owner",
    }


A = headers()
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(TENANT_B, WORKSPACE_B)


def _forget_process_memory() -> None:
    """Clear every in-memory store, as a restarted API process would have them."""
    skill_store.clear()
    governance_store.clear()
    phase5.store.clear()
    file_store.records.clear()
    onboarding._notifications.clear()


@pytest.fixture
def postgres_backend(
    database_engine: Engine,
    seed_scopes: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Route the control-plane stores to the test database as the non-owner app role."""
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(file_store, "storage", InMemoryObjectStorage())
    _forget_process_memory()
    yield
    _forget_process_memory()


@pytest.fixture
def client(postgres_backend: None) -> TestClient:
    return TestClient(app)


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return connection.execute(text(f"select count(*) from {table}")).scalar_one()  # nosec B608 - fixed table names


# Skills ---------------------------------------------------------------------------------


SKILL = {
    "skill_id": "acme.research", "version": "1.0.0", "name": "Research",
    "description": "Research with governed tools", "instructions": "Verify all sources.",
    "required_tools": ["web.search"], "risk_level": "medium",
}


def test_skill_registry_survives_a_restart_and_is_scoped(client: TestClient, database_engine: Engine) -> None:
    assert client.post("/api/v1/skills/versions", headers=A, json=SKILL).status_code == 201
    installed = client.post("/api/v1/skills/installations", headers=A, json={
        "skill_id": "acme.research", "version": "1.0.0", "approved_tools": ["web.search"]})
    assert installed.status_code == 201, installed.text
    assert client.post("/api/v1/skills/versions", headers=A, json=SKILL).status_code == 409

    _forget_process_memory()
    assert _count(database_engine, "skill_versions") == 1
    with open_skill_store(tenant_context()) as store:
        assert [v.skill_id for v in store.list_versions(tenant_context())] == ["acme.research"]
        [installation] = store.list_installations(tenant_context())
        assert installation.approved_tools == ["web.search"] and installation.enabled is True

    resolved = client.post("/api/v1/skills/resolve", headers=A, json={
        "skill_id": "acme.research", "available_tools": ["web.search"], "maximum_risk": "medium"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["instructions"] == "Verify all sources."

    # Versions belong to the tenant (visible from its other workspace); installations do not.
    assert [v["skill_id"] for v in client.get("/api/v1/skills/versions", headers=A2).json()] == ["acme.research"]
    assert client.get("/api/v1/skills/installations", headers=A2).json() == []
    assert client.get("/api/v1/skills/versions", headers=B).json() == []
    assert client.get("/api/v1/skills/installations", headers=B).json() == []
    assert client.post("/api/v1/skills/installations", headers=B, json={
        "skill_id": "acme.research", "version": "1.0.0"}).status_code == 404

    disabled = client.patch("/api/v1/skills/installations/acme.research", headers=A, json={"enabled": False})
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    assert client.delete("/api/v1/skills/installations/acme.research", headers=A).status_code == 204
    assert _count(database_engine, "skill_installations") == 0


# Governance -----------------------------------------------------------------------------


POLICY = {
    "name": "External actions",
    "description": "Protect consequential operations",
    "rules": [{"action": "integration.*.write", "effect": "require_approval", "conditions": {"max": 2}}],
}


def test_governance_and_its_audit_trail_survive_a_restart(client: TestClient, database_engine: Engine) -> None:
    first = client.post("/api/v1/policy-packs", headers=A, json=POLICY).json()
    second = client.post("/api/v1/policy-packs", headers=A, json={**POLICY, "name": "external ACTIONS"}).json()
    assert (first["version"], second["version"]) == (1, 2)
    assert client.post("/api/v1/role-templates", headers=A, json={
        "name": "Operator", "permissions": ["task:run", "task:read", "task:run"]}).status_code == 201
    assert client.post("/api/v1/role-templates", headers=A2, json={
        "name": "operator", "permissions": ["task:read"]}).status_code == 409
    assert client.post("/api/v1/organization/approval-rules", headers=A, json={
        "name": "Finance dual control", "action_pattern": "finance.*", "minimum_approvers": 2}).status_code == 201
    assert client.put("/api/v1/organization/memory-governance", headers=A, json={
        "default_retention_days": 90, "allowed_source_types": ["task"]}).status_code == 200
    activated = client.post(f"/api/v1/policy-packs/{first['id']}/activate", headers=A)
    assert activated.status_code == 200 and activated.json()["active"] is True

    _forget_process_memory()
    with open_governance_store(tenant_context()) as store:
        packs = store.list_policy_packs(tenant_context())
        assert [(p.version, p.active) for p in packs] == [(1, True), (2, False)]
        assert packs[0].rules[0].conditions == {"max": 2}
        assert [t.permissions for t in store.list_role_templates(tenant_context())] == [["task:read", "task:run"]]
        governance = store.get_memory_governance(tenant_context())
        assert governance is not None and governance.default_retention_days == 90

    # Governance settings are tenant-level: the tenant's other workspace sees them.
    summary_a2 = client.get("/api/v1/organization/governance", headers=A2).json()
    assert summary_a2 == {
        "tenant_id": TENANT_A, "policy_packs": 2, "active_policy_packs": 1,
        "role_templates": 1, "approval_rules": 1, "memory_governance_configured": True,
    }
    summary_b = client.get("/api/v1/organization/governance", headers=B).json()
    assert summary_b["policy_packs"] == 0 and summary_b["memory_governance_configured"] is False
    assert client.get("/api/v1/organization/memory-governance", headers=B).status_code == 404
    assert client.get("/api/v1/policy-packs", headers=B).json() == []

    # Audit records are append-only rows in audit_records, scoped to the acting workspace.
    exported = client.get("/api/v1/audit/export", headers=A).json()
    assert [row["action"] for row in exported] == [
        "policy_pack.created", "policy_pack.created", "role_template.created",
        "approval_rule.created", "memory_governance.updated", "policy_pack.activated",
    ]
    assert {row["tenant_id"] for row in exported} == {TENANT_A}
    assert client.get("/api/v1/audit/export", headers=A2).json() == []
    assert client.get("/api/v1/audit/export", headers=B).json() == []
    assert _count(database_engine, "audit_records") == 6


def test_a_rejected_governance_write_leaves_no_audit_record(client: TestClient, database_engine: Engine) -> None:
    assert client.post("/api/v1/role-templates", headers=A, json={
        "name": "Operator", "permissions": ["task:read"]}).status_code == 201
    assert client.post("/api/v1/role-templates", headers=A, json={
        "name": "OPERATOR", "permissions": ["task:read"]}).status_code == 409
    assert _count(database_engine, "role_templates") == 1
    assert _count(database_engine, "audit_records") == 1


# Marketplace and routing ----------------------------------------------------------------


PACKAGE = {
    "id": "skill.research-core", "name": "Research Core", "kind": "skill", "version": "1.0.0",
    "publisher": "ANUM", "verified": True, "permissions": ["memory:read"], "regions": ["us-east"],
}
TARGET = {
    "id": "eu-primary", "region": "eu-west", "provider": "openai-compatible", "model": "primary",
    "status": "healthy", "cost_per_1k_tokens": 0.012, "latency_ms": 200,
}


def test_marketplace_and_routing_survive_a_restart_and_are_scoped(client: TestClient, database_engine: Engine) -> None:
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=A, json=PACKAGE).status_code == 200
    assert client.post(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=A, json={}).status_code == 201
    assert client.put(f"/api/v1/routing/targets/{TARGET['id']}", headers=A, json=TARGET).status_code == 200

    _forget_process_memory()
    with open_scale_store(tenant_context()) as store:
        assert [p.id for p in store.catalog(tenant_context())] == [PACKAGE["id"]]
        assert [i.package_id for i in store.installs(tenant_context())] == [PACKAGE["id"]]
        assert [t.id for t in store.targets(tenant_context())] == ["configured-model", "eu-primary"]

    # Catalog and routing targets are tenant-level; installs are per workspace.
    assert [p["id"] for p in client.get("/api/v1/marketplace/packages", headers=A2).json()] == [PACKAGE["id"]]
    assert client.get("/api/v1/marketplace/installs", headers=A2).json() == []
    assert client.get("/api/v1/marketplace/packages", headers=B).json() == []
    assert [t["id"] for t in client.get("/api/v1/routing/targets", headers=B).json()] == ["configured-model"]
    assert client.post(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=B, json={}).status_code == 404
    assert client.delete(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=B).status_code == 404

    # The install in workspace A is invisible from A2 under RLS, yet still blocks the delete.
    assert client.delete(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=A2).status_code == 409
    assert client.delete(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=A).status_code == 204
    assert client.delete(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=A2).status_code == 204
    assert _count(database_engine, "marketplace_packages") == 0
    operations = client.get("/api/v1/enterprise/operations", headers=A).json()
    assert operations["installed_packages"] == 0 and operations["active_regions"] == 2


# Integrations, files and notification preferences ---------------------------------------


def test_integration_configuration_survives_a_restart_and_is_workspace_scoped(
    client: TestClient, database_engine: Engine
) -> None:
    saved = client.put("/api/v1/integrations/nats/configuration", headers=A, json={
        "enabled": False, "endpoint": "nats://nats.internal:4222"})
    assert saved.status_code == 200, saved.text

    restarted = default_integration_registry(settings)  # a new process' registry
    view = restarted.configuration("nats", tenant_context())
    assert (view.enabled, view.endpoint) == (False, "nats://nats.internal:4222")
    assert restarted.memory_configurations.list_configurations(tenant_context()) == {}
    for scope in (tenant_context(TENANT_A, WORKSPACE_A2), tenant_context(TENANT_B, WORKSPACE_B)):
        assert restarted.configuration("nats", scope).endpoint == settings.nats_url
    assert _count(database_engine, "integration_configurations") == 1


def test_file_metadata_survives_a_restart_and_bytes_stay_in_object_storage(
    client: TestClient, database_engine: Engine
) -> None:
    uploaded = client.post("/api/v1/files", headers={**A, "x-file-name": "report.txt", "content-type": "text/plain"},
                           content=b"phase two")
    assert uploaded.status_code == 201, uploaded.text
    record = uploaded.json()

    _forget_process_memory()
    with open_file_metadata_store(tenant_context()) as store:
        [stored] = store.list_files(tenant_context(), 10)
    assert stored.id == record["id"] and stored.sha256 == record["sha256"]
    assert client.get(f"/api/v1/files/{record['id']}/content", headers=A).content == b"phase two"
    with database_engine.connect() as connection:
        columns = set(connection.execute(text(
            "select column_name from information_schema.columns where table_name = 'workspace_files'"
        )).scalars())
    assert "content" not in columns and "storage_key" in columns

    for other in (A2, B):
        assert client.get(f"/api/v1/files/{record['id']}", headers=other).status_code == 404
        assert client.get("/api/v1/files", headers=other).json() == []
        assert client.delete(f"/api/v1/files/{record['id']}", headers=other).status_code == 404

    assert client.delete(f"/api/v1/files/{record['id']}", headers=A).status_code == 204
    assert _count(database_engine, "workspace_files") == 0
    assert files_module.file_store.storage.objects == {}  # type: ignore[attr-defined]


def test_file_upload_without_an_onboarded_workspace_keeps_no_bytes(client: TestClient) -> None:
    response = client.post(
        "/api/v1/files",
        headers={**headers(workspace_id="workspace_not_onboarded"), "x-file-name": "a.txt"},
        content=b"orphan?",
    )
    assert response.status_code == 409
    assert files_module.file_store.storage.objects == {}  # type: ignore[attr-defined]


def test_notification_preferences_survive_a_restart_per_user_and_workspace(
    client: TestClient, database_engine: Engine
) -> None:
    saved = client.put("/api/v1/notification-preferences", headers=A, json={"email_enabled": True, "run_failed": False})
    assert saved.status_code == 200, saved.text

    _forget_process_memory()
    with open_notification_preference_store(tenant_context()) as store:
        stored = store.get(tenant_context())
    assert stored == NotificationPreferences(email_enabled=True, run_failed=False)
    assert client.get("/api/v1/notification-preferences", headers=A).json()["email_enabled"] is True
    default = NotificationPreferences().model_dump()
    for other in (A2, B, headers(user_id="someone_else")):
        assert client.get("/api/v1/notification-preferences", headers=other).json() == default
    assert _count(database_engine, "notification_preferences") == 1


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/skills/versions", SKILL),
        ("post", "/api/v1/policy-packs", POLICY),
        ("put", f"/api/v1/marketplace/packages/{PACKAGE['id']}", PACKAGE),
        ("put", f"/api/v1/routing/targets/{TARGET['id']}", TARGET),
        ("put", "/api/v1/integrations/nats/configuration", {"enabled": True}),
        ("put", "/api/v1/notification-preferences", {}),
    ],
)
def test_writes_before_onboarding_are_refused(client: TestClient, method: str, path: str, body: dict) -> None:
    other_tenant = headers("tenant_not_onboarded", "workspace_not_onboarded")
    response = getattr(client, method)(path, headers=other_tenant, json=body)
    assert response.status_code == 409, response.text


# Raw RLS under the app role -------------------------------------------------------------


# One minimal row per table, written for TENANT_B / WORKSPACE_B.
CROSS_TENANT_INSERTS = {
    "skill_versions": "insert into skill_versions (id, tenant_id, skill_id, version, name, description, "
    "instructions, risk_level, created_by) values ('sv_x', :t, 's.x', '1.0.0', 'x', 'x', 'x', 'low', 'u')",
    "policy_packs": "insert into policy_packs (id, tenant_id, name, version, active, created_by) "
    "values ('pp_x', :t, 'x', 1, true, 'u')",
    "role_templates": "insert into role_templates (id, tenant_id, name) values ('rt_x', :t, 'x')",
    "approval_rules": "insert into approval_rules (id, tenant_id, name, action_pattern, minimum_approvers) "
    "values ('ar_x', :t, 'x', 'x.*', 1)",
    "memory_governance": "insert into memory_governance (tenant_id, default_retention_days, "
    "allow_permanent_retention, require_provenance, updated_by) values (:t, 30, false, true, 'u')",
    "marketplace_packages": "insert into marketplace_packages (tenant_id, id, name, kind, version, publisher, "
    "verified) values (:t, 'p.x', 'x', 'skill', '1.0.0', 'x', false)",
    "routing_targets": "insert into routing_targets (tenant_id, id, region, provider, model, status, "
    "cost_per_1k_tokens, latency_ms) values (:t, 'r.x', 'eu', 'p', 'm', 'healthy', 0, 1)",
    "integration_configurations": "insert into integration_configurations (tenant_id, workspace_id, "
    "integration_id, enabled, updated_by) values (:t, :w, 'nats', true, 'u')",
    "workspace_files": "insert into workspace_files (id, tenant_id, workspace_id, name, content_type, "
    "size_bytes, sha256, storage_key, created_by) values ('f_x', :t, :w, 'x', 'text/plain', 1, 'x', 'k', 'u')",
    "notification_preferences": "insert into notification_preferences (tenant_id, workspace_id, user_id, "
    "task_completed, approval_required, run_failed, automation_failed, email_enabled, desktop_enabled) "
    "values (:t, :w, 'u', true, true, true, true, false, true)",
}


@pytest.mark.parametrize("table", sorted(CROSS_TENANT_INSERTS))
def test_rls_rejects_cross_tenant_writes(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]], table: str
) -> None:
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(CROSS_TENANT_INSERTS[table]), {"t": TENANT_B, "w": WORKSPACE_B})


@pytest.mark.parametrize("table", sorted(CROSS_TENANT_INSERTS))
def test_rls_hides_rows_from_other_tenants_and_without_context(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]], table: str
) -> None:
    with app_session(tenant_context(TENANT_B, WORKSPACE_B), commit=True) as session:
        session.execute(text(CROSS_TENANT_INSERTS[table]), {"t": TENANT_B, "w": WORKSPACE_B})

    count_sql = text(f"select count(*) from {table}")  # nosec B608 - fixed table names
    with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
        assert session.execute(count_sql).scalar_one() == 1
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        assert session.execute(count_sql).scalar_one() == 0
    with app_session() as session:
        assert session.execute(count_sql).scalar_one() == 0


def test_rls_scopes_workspace_tables_to_the_workspace(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    workspace_tables = ("integration_configurations", "workspace_files", "notification_preferences")
    with app_session(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as session:
        for table in workspace_tables:
            session.execute(text(CROSS_TENANT_INSERTS[table]), {"t": TENANT_A, "w": WORKSPACE_A})
    with app_session(tenant_context(TENANT_A, WORKSPACE_A2)) as session:
        for table in workspace_tables:
            assert session.execute(text(f"select count(*) from {table}")).scalar_one() == 0  # nosec B608
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A2)) as session:
            session.execute(text(CROSS_TENANT_INSERTS["workspace_files"].replace("'f_x'", "'f_y'").replace("'k'", "'k2'")),
                            {"t": TENANT_A, "w": WORKSPACE_A})


def test_every_control_plane_table_is_under_forced_rls(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "select relname, relrowsecurity, relforcerowsecurity from pg_class "
                "where relnamespace = 'public'::regnamespace and relname = any(:names)"
            ),
            {"names": list(CONTROL_PLANE_TABLES)},
        ).all()
        policies = set(connection.execute(
            text("select tablename from pg_policies where schemaname = 'public'")
        ).scalars())
    assert {row.relname for row in rows} == set(CONTROL_PLANE_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
    assert set(CONTROL_PLANE_TABLES) <= policies
