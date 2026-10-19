"""Integration health history and credentials, skill history and rollback on PostgreSQL.

Runs the API's stores as the non-owner app role under RLS (migration 0026), and rotates
stored integration credentials across tenants through ``anum_maintenance`` only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from skill_signing_support import signed
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import main, phase5
from anum_api.db import session as db_session
from anum_api.db.workspace_settings_repository import SqlAlchemyIntegrationConfigurationStore
from anum_api.governance import governance_store
from anum_api.integrations import (
    CredentialMetadata,
    IntegrationDefinition,
    IntegrationKind,
    IntegrationRegistry,
    StoredCredential,
    external_webhook_integration,
)
from anum_api.rotate_secrets import rotate_integration_credentials
from anum_api.schemas import utc_now
from anum_api.secret_box import SecretCipher, reset_secret_cipher
from anum_api.settings import settings
from anum_api.skills_api import skill_store

pytestmark = pytest.mark.database

OLD_KEY = Fernet.generate_key()
NEW_KEY = Fernet.generate_key()
SECRET = "example-workspace-abcd"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, roles: str = "owner,org_admin") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": "user_test", "x-user-roles": roles}


class Probe:
    def __init__(self) -> None:
        self.healthy = True

    async def __call__(self) -> None:
        if not self.healthy:
            raise ConnectionRefusedError("refused")


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None) -> sessionmaker[Session]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    return factory


@pytest.fixture
def client(app_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Probe]]:
    probe = Probe()
    registry = IntegrationRegistry([
        IntegrationDefinition(
            id="cache", name="Cache", kind=IntegrationKind.CACHE, endpoint="redis://cache:6379",
            credentials=CredentialMetadata(configured=True), probe=probe, remediation="Check Valkey.",
        ),
        external_webhook_integration(settings.model_copy(update={"external_webhook_url": "https://hooks.example/a"})),
    ])
    monkeypatch.setattr(db_session, "SessionLocal", app_factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(main, "integration_registry", registry)
    skill_store.clear()
    governance_store.clear()
    phase5.store.clear()
    reset_secret_cipher()
    yield TestClient(main.app), probe
    reset_secret_cipher()


def _rows(database_engine: Engine, sql: str) -> list[tuple]:
    with database_engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql))]


def test_health_history_and_credentials_persist_under_rls_with_audit(
    client: tuple[TestClient, Probe], database_engine: Engine
) -> None:
    api, probe = client
    probe.healthy = False
    tested = api.post("/api/v1/integrations/cache/test", headers=headers())
    assert tested.status_code == 200, tested.text
    probe.healthy = True
    listed = {item["id"]: item for item in api.get("/api/v1/integrations", headers=headers()).json()}
    assert listed["cache"]["status"] == "connected"
    assert listed["cache"]["last_error"] == "Health check failed: ConnectionRefusedError"

    saved = api.put("/api/v1/integrations/external-webhook/credential", headers=headers(), json={"secret": SECRET})
    assert saved.status_code == 200, saved.text
    stored = _rows(database_engine, "select tenant_id, workspace_id, ciphertext, credential_hint from integration_credentials")
    assert len(stored) == 1 and stored[0][:2] == (TENANT_A, WORKSPACE_A)
    assert SECRET not in stored[0][2] and stored[0][3] == "…abcd"

    # Another workspace of the same tenant sees neither the history nor the credential.
    other = {item["id"]: item for item in api.get("/api/v1/integrations", headers=headers(workspace_id=WORKSPACE_A2)).json()}
    assert other["cache"]["last_error"] is None
    assert other["external-webhook"]["workspace_credential"]["configured"] is False

    audit = _rows(database_engine, "select workspace_id, action, outcome, metadata::text from audit_records order by created_at")
    assert [(row[1], row[2]) for row in audit] == [("integration.tested", "failed"), ("integration.credential_set", "success")]
    assert all(SECRET not in row[3] for row in audit)

    assert api.delete("/api/v1/integrations/external-webhook/credential", headers=headers()).status_code == 204
    assert _rows(database_engine, "select count(*) from integration_credentials") == [(0,)]


def test_rls_hides_other_workspaces_integration_rows(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> None:
    context = tenant_context(TENANT_A, WORKSPACE_A)
    credential = StoredCredential(ciphertext="ciphertext-example", hint="…abcd", updated_by="user_test", updated_at=utc_now())
    with app_session(context, commit=True) as session:
        SqlAlchemyIntegrationConfigurationStore(session).save_credential(context, "external-webhook", credential)
    other = tenant_context(TENANT_B, WORKSPACE_B)
    with app_session(other) as session:
        assert session.execute(text("select count(*) from integration_credentials")).scalar_one() == 0
        # Writing into another tenant's workspace is refused by the RLS check, not just the store.
        with pytest.raises(DBAPIError, match="row-level security"):
            session.execute(text(
                "insert into integration_credentials (tenant_id, workspace_id, integration_id, ciphertext, "
                "credential_hint, updated_by, updated_at) values (:t, :w, 'external-webhook', 'x', 'x', 'x', now())"
            ), {"t": TENANT_A, "w": WORKSPACE_A})


def test_rotation_re_encrypts_integration_credentials_across_tenants(
    app_factory: sessionmaker[Session], app_session: Callable[..., Iterator[Session]], database_engine: Engine
) -> None:
    old = SecretCipher([OLD_KEY])
    for tenant_id, workspace_id in ((TENANT_A, WORKSPACE_A), (TENANT_A, WORKSPACE_A2), (TENANT_B, WORKSPACE_B)):
        context = tenant_context(tenant_id, workspace_id)
        with app_session(context, commit=True) as session:
            SqlAlchemyIntegrationConfigurationStore(session).save_credential(
                context, "external-webhook",
                StoredCredential(ciphertext=old.encrypt(f"example-{workspace_id}"), hint="…abcd",
                                 updated_by="user_test", updated_at=utc_now()),
            )
    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    dry = rotate_integration_credentials(cipher, session_factory=app_factory, dry_run=True)
    assert (dry.scanned, dry.rotated) == (3, 3)
    report = rotate_integration_credentials(cipher, session_factory=app_factory, batch_size=2)
    assert (report.scanned, report.rotated, report.failed) == (3, 3, 0)
    values = _rows(database_engine, "select workspace_id, ciphertext from integration_credentials order by 1")
    assert [SecretCipher([NEW_KEY]).decrypt(row[1]) for row in values] == [f"example-{row[0]}" for row in values]
    audit = _rows(database_engine, "select action, target, outcome from audit_records order by workspace_id")
    assert audit == [("secrets.rotated", "integration:external-webhook", "succeeded")] * 3
    again = rotate_integration_credentials(cipher, session_factory=app_factory)
    assert (again.rotated, again.already_current) == (0, 3)

    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert len(connection.execute(text("select tenant_id, workspace_id, integration_id from integration_credentials")).all()) == 3
        with pytest.raises(DBAPIError, match="permission denied"):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select ciphertext from integration_credentials"))


SKILL = {"skill_id": "acme.notes", "name": "Notes", "description": "Writes notes", "instructions": "Write.",
         "risk_level": "low"}


def test_skill_rollback_and_history_persist(client: tuple[TestClient, Probe], database_engine: Engine) -> None:
    api, _ = client
    for version, tools in (("1.0.0", ["anum.respond"]), ("1.1.0", ["anum.respond", "external.action"])):
        assert api.post("/api/v1/skills/versions", headers=headers(),
                        json=signed(api, headers(), {**SKILL, "version": version, "required_tools": tools})).status_code == 201
    assert api.post("/api/v1/skills/installations", headers=headers(), json={
        "skill_id": "acme.notes", "version": "1.1.0", "approved_tools": ["anum.respond", "external.action"]}).status_code == 201
    owner_only = headers(roles="owner")
    assert api.post("/api/v1/skills/installations/acme.notes/rollback", headers=owner_only,
                    json={"version": "1.0.0"}).status_code == 403
    rolled = api.post("/api/v1/skills/installations/acme.notes/rollback", headers=headers(), json={"version": "1.0.0"})
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["approved_tools"] == ["anum.respond"]

    skill_store.clear()
    governance_store.clear()  # nothing is read back from process memory
    detail = api.get("/api/v1/skills/acme.notes", headers=headers()).json()
    assert detail["installation"]["version"] == "1.0.0"
    assert [h["action"] for h in detail["history"]] == [
        "skill.rolled_back", "skill.installed", "skill.published", "skill.published"]
    assert detail["history"][0]["metadata"]["from_version"] == "1.1.0"
    # Installation history is per workspace; the publish records belong to the publishing workspace.
    assert api.get("/api/v1/skills/acme.notes", headers=headers(workspace_id=WORKSPACE_A2)).json()["history"] == []
    assert _rows(database_engine, "select version from skill_installations") == [("1.0.0",)]
