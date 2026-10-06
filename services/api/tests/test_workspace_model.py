import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import onboarding
from anum_api.main import app
from anum_api.model_gateway import MockModelGateway
from anum_api.onboarding import _model_configs, workspace_model_gateway
from anum_api.schemas import TenantContext
from anum_api.voice import voice_store


client = TestClient(app)
headers = {
    "x-tenant-id": "tenant_model",
    "x-workspace-id": "workspace_model",
    "x-user-id": "user_model",
    "x-user-roles": "owner",
}
OLLAMA_CONFIG = {"provider": "ollama", "model": "llama3.2", "base_url": "http://localhost:11434/v1"}


def setup_function() -> None:
    _model_configs.clear()
    onboarding._workspace_gateways.clear()
    voice_store.clear()


def _ollama(monkeypatch: pytest.MonkeyPatch, answer: str) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"model": "llama3.2", "choices": [{"message": {"content": answer}, "finish_reason": "stop"}]},
        )

    monkeypatch.setattr(
        onboarding,
        "_test_client_factory",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)),
    )
    return seen


def test_workspace_without_a_saved_model_uses_the_server_default() -> None:
    fallback = MockModelGateway()
    context = TenantContext(tenant_id="tenant_model", workspace_id="workspace_model", user_id="user_model")

    assert workspace_model_gateway(context, fallback) is fallback


def test_voice_answers_come_from_the_model_saved_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _ollama(monkeypatch, "Focus on the two approvals waiting for you.")
    assert client.put("/api/v1/model-config", headers=headers, json=OLLAMA_CONFIG).status_code == 200

    session_id = client.post("/api/v1/voice/sessions", headers=headers, json={"locale": "en-US"}).json()["id"]
    segment_id = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": "What should I focus on today?", "is_final": True, "client_sequence": 0},
    ).json()["id"]
    response = client.post(
        f"/api/v1/voice/sessions/{session_id}/ask", headers=headers, json={"transcript_segment_id": segment_id}
    )

    assert response.status_code == 200
    assert "two approvals" in response.json()["reply"]
    assert str(seen[0].url) == "http://localhost:11434/v1/chat/completions"
    assert b"llama3.2" in seen[0].read()


def test_task_runs_use_the_model_saved_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _ollama(monkeypatch, "Here is the summary you asked for.")
    assert client.put("/api/v1/model-config", headers=headers, json=OLLAMA_CONFIG).status_code == 200

    task = client.post(
        "/api/v1/tasks", headers=headers, json={"title": "Summarize", "prompt": "Summarize the week"}
    ).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers)

    assert run.status_code == 200, run.text
    assert seen, "the task run never called the workspace model"
    assert all(str(request.url).startswith("http://localhost:11434/v1") for request in seen)


def test_saving_a_new_model_replaces_the_cached_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    _ollama(monkeypatch, "ok")
    context = TenantContext(tenant_id="tenant_model", workspace_id="workspace_model", user_id="user_model")
    fallback = MockModelGateway()
    client.put("/api/v1/model-config", headers=headers, json=OLLAMA_CONFIG)
    first = workspace_model_gateway(context, fallback)

    client.put("/api/v1/model-config", headers=headers, json={**OLLAMA_CONFIG, "model": "qwen2.5"})
    second = workspace_model_gateway(context, fallback)

    assert first is not fallback
    assert second is not first
    assert workspace_model_gateway(context, fallback) is second
