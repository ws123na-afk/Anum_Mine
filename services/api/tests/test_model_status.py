"""GET /api/v1/model-status: whether this workspace's answers come from the placeholder model.

The web app shows a banner from it (docs/model-gateway.md#placeholder-answers).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.model_config_store import open_model_config_store
from anum_api.model_status import workspace_model_status
from anum_api.schemas import TenantContext
from anum_api.settings import settings

client = TestClient(main.app)
TENANT, WORKSPACE = "tenant_model_status", "ws_model_status"
HEADERS = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "member_1", "x-user-roles": "member"}
CTX = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_1", roles=["owner"])


@pytest.fixture(autouse=True)
def _onboarded() -> None:
    owner = {**HEADERS, "x-user-id": "owner_1", "x-user-roles": "owner"}
    response = client.put("/api/v1/onboarding", headers=owner, json={"organization_name": "Model Co", "workspace_name": "Main"})
    assert response.status_code == 200, response.text


def test_server_mock_is_a_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_provider", "mock")
    response = client.get("/api/v1/model-status", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"placeholder": True, "provider": "mock", "source": "server"}


def test_server_real_model_is_not_a_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_provider", "openai_compatible")
    assert client.get("/api/v1/model-status", headers=HEADERS).json() == {
        "placeholder": False, "provider": "openai-compatible", "source": "server"}


def test_a_workspace_model_wins_over_the_server_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_provider", "mock")
    with open_model_config_store(CTX) as store:
        store.save(CTX, provider="ollama", model="llama3.2", base_url="http://localhost:11434/v1", api_key=None,
                   updated_at=datetime.now(UTC), routing={}, keep_api_key=False)
    try:
        status = workspace_model_status(CTX)
        assert (status.placeholder, status.provider, status.source) == (False, "ollama", "workspace")
        assert client.get("/api/v1/model-status", headers=HEADERS).json()["source"] == "workspace"
    finally:
        with open_model_config_store(CTX) as store:
            store.save(CTX, provider="mock", model="anum-mock-planner", base_url="http://localhost", api_key=None,
                       updated_at=datetime.now(UTC), routing={}, keep_api_key=False)
    assert client.get("/api/v1/model-status", headers=HEADERS).json() == {
        "placeholder": True, "provider": "mock", "source": "workspace"}


def test_model_status_needs_a_member() -> None:
    assert client.get("/api/v1/model-status").status_code in {401, 403}
