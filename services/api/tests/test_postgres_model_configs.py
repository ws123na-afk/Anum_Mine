"""PostgreSQL persistence, encryption at rest and RLS for workspace model configs."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from conftest import (
    APP_ROLE,
    FIXED_NOW,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    tenant_context,
)
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import onboarding
from anum_api.db import session as db_session
from anum_api.db.model_config_repository import SqlAlchemyModelConfigStore
from anum_api.main import app
from anum_api.model_config_store import WorkspaceNotProvisionedError
from anum_api.model_gateway import MockModelGateway, OpenAICompatibleGateway
from anum_api.secret_box import SecretCipher, reset_secret_cipher
from anum_api.settings import settings

pytestmark = pytest.mark.database

API_KEY = "sk-live-PERSISTED-secret-4321"
SECRETS_KEY = Fernet.generate_key()


def _store(session: Session) -> SqlAlchemyModelConfigStore:
    return SqlAlchemyModelConfigStore(session, SecretCipher([SECRETS_KEY]))


def _save(session: Session, context, **overrides) -> None:
    values = {
        "provider": "openai_compatible",
        "model": "gpt-4.1-mini",
        "base_url": "https://models.example/v1",
        "api_key": API_KEY,
        "updated_at": FIXED_NOW,
    }
    values.update(overrides)
    _store(session).save(context, **values)


def test_config_round_trips_with_the_key_encrypted_at_rest(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
    database_engine: Engine,
) -> None:
    with app_session(tenant_context(), commit=True) as session:
        _save(session, tenant_context())

    with database_engine.connect() as connection:
        row = connection.execute(
            text("select api_key_ciphertext, credential_hint, updated_by_user_id from workspace_model_configs")
        ).one()
    assert API_KEY not in row.api_key_ciphertext
    assert "PERSISTED" not in row.api_key_ciphertext
    assert row.credential_hint == "...4321"
    assert row.updated_by_user_id == "user_test"

    with app_session(tenant_context()) as session:
        full = _store(session).get(tenant_context())
        summary = _store(session).get(tenant_context(), include_secret=False)

    assert full is not None and full.api_key is not None
    assert full.api_key.get_secret_value() == API_KEY
    assert full.updated_at == FIXED_NOW
    assert summary is not None and summary.api_key is None
    assert summary.credential_configured is True
    assert summary.credential_hint == "...4321"


def test_saving_again_replaces_the_config(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with app_session(tenant_context(), commit=True) as session:
        _save(session, tenant_context())
    with app_session(tenant_context(), commit=True) as session:
        _save(session, tenant_context(), provider="ollama", model="llama3.2", base_url="http://localhost:11434/v1", api_key=None)

    with app_session(tenant_context()) as session:
        config = _store(session).get(tenant_context())
        count = session.execute(text("select count(*) from workspace_model_configs")).scalar_one()

    assert count == 1
    assert config is not None
    assert config.provider == "ollama"
    assert config.api_key is None and config.credential_configured is False
    assert config.usable is True


def test_rls_isolates_model_configs_between_tenants_and_workspaces(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with app_session(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as session:
        _save(session, tenant_context(TENANT_A, WORKSPACE_A))

    for context in (tenant_context(TENANT_B, WORKSPACE_B), tenant_context(TENANT_A, WORKSPACE_A2)):
        with app_session(context) as session:
            assert session.execute(text("select count(*) from workspace_model_configs")).scalar_one() == 0
            assert _store(session).get(context) is None

    with app_session() as session:
        assert session.execute(text("select count(*) from workspace_model_configs")).scalar_one() == 0


def test_rls_rejects_cross_tenant_model_config_writes(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(
                text(
                    """
                    insert into workspace_model_configs (tenant_id, workspace_id, provider, model, base_url)
                    values (:tenant_id, :workspace_id, 'mock', 'm', 'http://localhost')
                    """
                ),
                {"tenant_id": TENANT_B, "workspace_id": WORKSPACE_B},
            )


def test_database_rejects_hosted_provider_without_ciphertext(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(
                text(
                    """
                    insert into workspace_model_configs (tenant_id, workspace_id, provider, model, base_url)
                    values (:tenant_id, :workspace_id, 'openai_compatible', 'm', 'https://x')
                    """
                ),
                {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
            )


def test_saving_for_an_unprovisioned_workspace_is_refused(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    missing = tenant_context(TENANT_A, "workspace_missing")
    with pytest.raises(WorkspaceNotProvisionedError):
        with app_session(missing) as session:
            _save(session, missing)


@pytest.fixture
def postgres_backend(
    database_engine: Engine,
    seed_scopes: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Route the API's model config store to the test database as the non-owner app role."""
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "secrets_key", SecretStr(SECRETS_KEY.decode()))
    reset_secret_cipher()
    onboarding._workspace_gateways.clear()
    yield
    onboarding._workspace_gateways.clear()
    reset_secret_cipher()


HEADERS = {
    "x-tenant-id": TENANT_A,
    "x-workspace-id": WORKSPACE_A,
    "x-user-id": "user_test",
    "x-user-roles": "owner",
}


def test_api_persists_model_config_in_postgres_and_never_returns_the_key(
    postgres_backend: None,
    database_engine: Engine,
) -> None:
    client = TestClient(app)

    saved = client.put(
        "/api/v1/model-config",
        headers=HEADERS,
        json={"provider": "openai_compatible", "model": "gpt-4.1-mini", "base_url": "https://models.example/v1", "api_key": API_KEY},
    )
    fetched = client.get("/api/v1/model-config", headers=HEADERS)
    other = client.get("/api/v1/model-config", headers={**HEADERS, "x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B})

    assert saved.status_code == 200, saved.text
    assert fetched.status_code == 200
    assert fetched.json()["credential_hint"] == "...4321"
    assert API_KEY not in saved.text and API_KEY not in fetched.text
    assert other.status_code == 404
    with database_engine.connect() as connection:
        stored = connection.execute(text("select api_key_ciphertext from workspace_model_configs")).scalar_one()
    assert API_KEY not in stored
    assert Fernet(SECRETS_KEY).decrypt(stored.encode()).decode() == API_KEY


def test_api_refuses_model_config_before_onboarding(postgres_backend: None) -> None:
    client = TestClient(app)

    response = client.put(
        "/api/v1/model-config",
        headers={**HEADERS, "x-workspace-id": "workspace_not_onboarded"},
        json={"provider": "mock", "model": "anum-mock", "base_url": "http://localhost:8000"},
    )

    assert response.status_code == 409


def test_workspace_gateway_is_built_from_the_persisted_config(
    postgres_backend: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"model": "gpt-4.1-mini", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    monkeypatch.setattr(onboarding, "_test_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    client = TestClient(app)
    fallback = MockModelGateway()
    context = tenant_context()

    assert onboarding.workspace_model_gateway(context, fallback) is fallback
    client.put(
        "/api/v1/model-config",
        headers=HEADERS,
        json={"provider": "openai_compatible", "model": "gpt-4.1-mini", "base_url": "https://models.example/v1", "api_key": API_KEY},
    )
    onboarding._workspace_gateways.clear()  # as if another API instance saved it

    gateway = onboarding.workspace_model_gateway(context, fallback)

    assert isinstance(gateway, OpenAICompatibleGateway)
    assert gateway.api_key == API_KEY
    assert onboarding.workspace_model_gateway(context, fallback) is gateway  # cached
    tested = client.post("/api/v1/model-config/test", headers=HEADERS)
    assert tested.status_code == 200, tested.text
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"

    client.put(
        "/api/v1/model-config",
        headers=HEADERS,
        json={"provider": "ollama", "model": "llama3.2", "base_url": "http://localhost:11434/v1"},
    )
    replaced = onboarding.workspace_model_gateway(context, fallback)
    assert replaced is not gateway
    assert replaced.provider == "ollama"
