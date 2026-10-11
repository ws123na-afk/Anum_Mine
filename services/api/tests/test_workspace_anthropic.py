"""Anthropic (Claude) as a per-workspace model choice (docs/model-gateway.md)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import onboarding
from anum_api.anthropic_gateway import AnthropicGateway
from anum_api.main import app
from anum_api.model_budget import BudgetedModelGateway
from anum_api.model_gateway import LOCAL_EMBEDDING_MODEL, MockModelGateway, ModelRoute, route_gateway
from anum_api.onboarding import _model_configs, budgeted_model_gateway, workspace_model_gateway
from anum_api.retrieval import build_embedder
from anum_api.schemas import TenantContext
from anum_api.settings import settings

client = TestClient(app)
HEADERS = {
    "x-tenant-id": "tenant_claude",
    "x-workspace-id": "workspace_claude",
    "x-user-id": "user_claude",
    "x-user-roles": "owner",
}
CONTEXT = TenantContext(tenant_id="tenant_claude", workspace_id="workspace_claude", user_id="user_claude")
API_KEY = "sk-ant-example-wxyz"
CLAUDE = {
    "provider": "anthropic",
    "model": "claude-sonnet-5-5",
    "api_key": API_KEY,
    "fast_model": "claude-haiku-5-5",
    "capable_model": "claude-sonnet-5-5",
    "fast_effort": "low",
    "capable_effort": "",
}


def setup_function() -> None:
    _model_configs.clear()
    onboarding._workspace_gateways.clear()


def _claude(monkeypatch: pytest.MonkeyPatch, *, status: int = 200, text: str = "ANUM_OK") -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"type": "error", "error": {"type": "authentication_error"}})
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_example",
                "model": body["model"],
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        )

    monkeypatch.setattr(
        onboarding, "_test_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))
    )
    return seen


def test_saving_anthropic_defaults_the_base_url_and_never_returns_the_key() -> None:
    saved = client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE)
    fetched = client.get("/api/v1/model-config", headers=HEADERS)

    assert saved.status_code == 200, saved.text
    body = fetched.json()
    assert body["provider"] == "anthropic"
    assert body["base_url"] == "https://api.anthropic.com"
    assert body["fast_model"] == "claude-haiku-5-5"
    assert body["capable_model"] == "claude-sonnet-5-5"
    assert body["fast_effort"] == "low"
    assert body["capable_effort"] is None  # empty means "not set"
    assert body["credential_configured"] is True and body["credential_hint"] == "...wxyz"
    assert API_KEY not in saved.text and API_KEY not in fetched.text


def test_an_openai_style_anthropic_base_url_is_stored_without_v1() -> None:
    saved = client.put(
        "/api/v1/model-config", headers=HEADERS, json={**CLAUDE, "base_url": "https://api.anthropic.com/v1/"}
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["base_url"] == "https://api.anthropic.com"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({**CLAUDE, "api_key": None}, "api_key"),
        ({**CLAUDE, "fast_effort": "turbo"}, "fast_effort"),
        ({**CLAUDE, "model": "   "}, "model"),
        (
            {"provider": "ollama", "model": "llama3.2", "base_url": "http://localhost:11434/v1", "fast_model": "x"},
            "anthropic provider only",
        ),
        ({"provider": "openai_compatible", "model": "gpt-4.1-mini", "api_key": API_KEY}, "base_url"),
    ],
)
def test_invalid_workspace_model_configs_are_refused(payload: dict[str, object], reason: str) -> None:
    response = client.put("/api/v1/model-config", headers=HEADERS, json=payload)

    assert response.status_code == 422
    assert reason in response.text
    assert API_KEY not in response.text


def test_a_routing_change_without_a_key_keeps_the_saved_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _claude(monkeypatch)
    assert client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE).status_code == 200
    # No api_key at all; the OpenAI-style base URL normalizes to the saved one.
    rerouted = {name: value for name, value in CLAUDE.items() if name != "api_key"}
    rerouted.update(fast_effort="medium", base_url="https://api.anthropic.com/v1/")

    saved = client.put("/api/v1/model-config", headers=HEADERS, json=rerouted)
    tested = client.post("/api/v1/model-config/test", headers=HEADERS)

    assert saved.status_code == 200, saved.text
    assert saved.json()["fast_effort"] == "medium"
    assert saved.json()["credential_configured"] is True and saved.json()["credential_hint"] == "...wxyz"
    assert tested.status_code == 200, tested.text
    assert seen and all(request.headers["x-api-key"] == API_KEY for request in seen)


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "https://claude-proxy.example"},
        {"provider": "openai_compatible", "base_url": "https://api.anthropic.com", "fast_model": None,
         "capable_model": None, "fast_effort": None, "capable_effort": None},
    ],
)
def test_a_new_provider_or_base_url_needs_the_key_again(change: dict[str, object]) -> None:
    assert client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE).status_code == 200

    response = client.put("/api/v1/model-config", headers=HEADERS, json={**CLAUDE, "api_key": None, **change})

    assert response.status_code == 422, response.text
    assert "provider or base URL changed" in response.text
    stored = client.get("/api/v1/model-config", headers=HEADERS).json()
    assert stored["provider"] == "anthropic" and stored["base_url"] == "https://api.anthropic.com"
    assert stored["fast_effort"] == "low"


def test_a_key_dropped_by_a_keyless_provider_is_not_brought_back() -> None:
    assert client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE).status_code == 200
    ollama = {"provider": "ollama", "model": "llama3.2", "base_url": "http://localhost:11434/v1"}
    assert client.put("/api/v1/model-config", headers=HEADERS, json=ollama).status_code == 200

    response = client.put("/api/v1/model-config", headers=HEADERS, json={**CLAUDE, "api_key": None})

    assert response.status_code == 422
    assert "no key is saved" in response.text


def test_only_owners_may_save_or_test_a_model() -> None:
    member = {**HEADERS, "x-user-roles": "member"}

    assert client.put("/api/v1/model-config", headers=member, json=CLAUDE).status_code == 403
    assert client.post("/api/v1/model-config/test", headers=member).status_code == 403


def test_the_workspace_gateway_routes_fast_and_capable_models_behind_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _claude(monkeypatch)
    assert client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE).status_code == 200

    gateway = workspace_model_gateway(CONTEXT, MockModelGateway())
    assert isinstance(gateway, AnthropicGateway)
    assert gateway.api_key == API_KEY
    assert gateway.base_url == "https://api.anthropic.com"
    fast = gateway.for_route(ModelRoute.FAST)
    capable = gateway.for_route(ModelRoute.CAPABLE)
    assert (fast.model, fast.effort) == ("claude-haiku-5-5", "low")
    assert (capable.model, capable.effort) == ("claude-sonnet-5-5", None)

    budgeted = budgeted_model_gateway(CONTEXT, MockModelGateway())
    routed = route_gateway(budgeted, ModelRoute.FAST)
    assert isinstance(routed, BudgetedModelGateway)
    assert routed.model == "claude-haiku-5-5"


def test_unset_routes_use_the_saved_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _claude(monkeypatch)
    payload = {"provider": "anthropic", "model": "claude-opus-5-5", "api_key": API_KEY}
    assert client.put("/api/v1/model-config", headers=HEADERS, json=payload).status_code == 200

    gateway = workspace_model_gateway(CONTEXT, MockModelGateway())

    assert gateway.for_route(ModelRoute.FAST).model == "claude-opus-5-5"
    assert gateway.for_route(ModelRoute.CAPABLE).model == "claude-opus-5-5"
    assert gateway.for_route(ModelRoute.FAST).effort is None


def test_task_planning_uses_the_capable_model(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _claude(monkeypatch, text="Here is the summary you asked for.")
    assert client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE).status_code == 200
    task = client.post(
        "/api/v1/tasks", headers=HEADERS, json={"title": "Summarize", "prompt": "Summarize the week"}
    ).json()

    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)

    assert run.status_code == 200, run.text
    assert seen, "the task run never called the workspace model"
    assert all(str(request.url) == "https://api.anthropic.com/v1/messages" for request in seen)
    assert {json.loads(request.content)["model"] for request in seen} == {"claude-sonnet-5-5"}
    assert seen[0].headers["x-api-key"] == API_KEY


def test_connection_test_makes_one_tiny_uncached_call_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _claude(monkeypatch)
    assert client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE).status_code == 200

    response = client.post("/api/v1/model-config/test", headers=HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-sonnet-5-5"
    assert body["models"] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    sent = [json.loads(request.content) for request in seen]
    assert [item["model"] for item in sent] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert all(item["max_tokens"] == 16 for item in sent)
    assert all("cache_control" not in json.dumps(item) for item in sent)
    assert "output_config" not in sent[0]
    assert sent[1]["output_config"] == {"effort": "low"}


def test_connection_test_with_one_model_calls_it_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _claude(monkeypatch)
    payload = {"provider": "anthropic", "model": "claude-haiku-5-5", "api_key": API_KEY}
    client.put("/api/v1/model-config", headers=HEADERS, json=payload)

    response = client.post("/api/v1/model-config/test", headers=HEADERS)

    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["claude-haiku-5-5"]
    assert len(seen) == 1


def test_a_rejected_key_says_how_to_fix_it_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _claude(monkeypatch, status=401)
    client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE)

    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/v1/model-config/test", headers=HEADERS)

    assert response.status_code == 502
    assert "Anthropic rejected the API key" in response.json()["error"]["message"]
    assert API_KEY not in response.text
    assert all(API_KEY not in record.getMessage() and API_KEY not in str(record.__dict__) for record in caplog.records)


def test_an_unknown_model_names_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _claude(monkeypatch, status=404)
    client.put("/api/v1/model-config", headers=HEADERS, json={**CLAUDE, "capable_model": "claude-nope"})

    response = client.post("/api/v1/model-config/test", headers=HEADERS)

    assert response.status_code == 502
    assert "claude-nope" in response.json()["error"]["message"]


def test_retrieval_uses_the_local_embedder_for_a_claude_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _claude(monkeypatch)
    client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE)
    # The server's embedding model belongs to the server's own provider.
    monkeypatch.setattr(settings, "embedding_backend", "gateway")
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    monkeypatch.setattr(settings, "model_provider", "openai-compatible")

    embedder = build_embedder(CONTEXT, MockModelGateway())
    result = asyncio.run(embedder.embed(["remember the quarterly plan"]))

    assert embedder.model_name == LOCAL_EMBEDDING_MODEL
    assert result.model == LOCAL_EMBEDDING_MODEL and len(result.vectors) == 1
    assert seen == []  # nothing left the API


def test_the_anthropic_gateway_still_refuses_an_explicit_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _claude(monkeypatch)
    client.put("/api/v1/model-config", headers=HEADERS, json=CLAUDE)
    gateway = workspace_model_gateway(CONTEXT, MockModelGateway())

    with pytest.raises(ValueError, match="no embeddings endpoint"):
        asyncio.run(gateway.embed(["x"], model="text-embedding-3-small"))


def test_routing_migration_is_expand_only_and_chained() -> None:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0024_model_config_routes.py"
    source = path.read_text(encoding="utf-8")

    assert 'revision = "0024_model_config_routes"' in source and len("0024_model_config_routes") <= 32
    assert 'down_revision = "0023_developer_one_active"' in source
    assert "'anthropic'" in source
    upgrade = source.split("def upgrade")[1].split("def downgrade")[0]
    assert "drop_column" not in upgrade and "drop_table" not in upgrade
    assert "nullable=True" in upgrade and "nullable=False" not in upgrade
