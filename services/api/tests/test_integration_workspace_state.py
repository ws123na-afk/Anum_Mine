"""Integration health history, test connection, workspace credentials and audit.

docs/tools-and-integrations.md#workspace-configuration. Memory backend; the PostgreSQL
stores are covered in test_postgres_integration_state.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_tools import ToolCall
from anum_api.governance import governance_store
from anum_api.integration_tools import configured_external_handler
from anum_api.integrations import (
    CredentialMetadata,
    IntegrationDefinition,
    IntegrationKind,
    IntegrationRegistry,
    IntegrationStatus,
    external_webhook_integration,
)
from anum_api.schemas import TenantContext
from anum_api.settings import settings

OWNER = {"x-tenant-id": "tenant_int", "x-workspace-id": "workspace_int", "x-user-id": "owner_int", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_int", "x-user-roles": "member"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_int_other"}
SECRET = "example-workspace-abcd"


def _context(workspace: str = "workspace_int") -> TenantContext:
    return TenantContext(tenant_id="tenant_int", workspace_id=workspace, user_id="owner_int", roles=["owner"])


class Switch:
    """A probe whose outcome the test flips."""

    def __init__(self) -> None:
        self.healthy = True
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        if not self.healthy:
            raise ConnectionRefusedError("refused by 10.0.0.5 for user admin")


def _registry(probe: Switch) -> IntegrationRegistry:
    return IntegrationRegistry([
        IntegrationDefinition(
            id="cache", name="Cache", kind=IntegrationKind.CACHE, endpoint="redis://cache:6379",
            credentials=CredentialMetadata(configured=True), probe=probe,
            environment_variable="ANUM_VALKEY_URL", remediation="Check that Valkey is running.",
        ),
        external_webhook_integration(settings.model_copy(update={"external_webhook_url": "https://hooks.example/actions"})),
    ])


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[Switch]:
    switch = Switch()
    registry = _registry(switch)

    async def reachable(host: str, port: int, payload: bytes | None = None) -> None:
        return None

    from anum_api import integrations

    monkeypatch.setattr(integrations, "tcp_probe", reachable)
    monkeypatch.setattr(main, "integration_registry", registry)
    governance_store.clear()
    yield switch
    governance_store.clear()


def _health(client: TestClient, headers: dict[str, str] = OWNER) -> dict[str, dict]:
    response = client.get("/api/v1/integrations", headers=headers)
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()}


def test_a_failure_is_kept_as_last_error_with_how_to_fix_after_recovery(probe: Switch) -> None:
    client = TestClient(main.app)
    probe.healthy = False
    failed = _health(client)["cache"]
    assert failed["status"] == "degraded"
    assert failed["remediation"] == "Check that Valkey is running."
    assert failed["environment_variable"] == "ANUM_VALKEY_URL"
    # Only the exception class: never hosts or users from the message.
    assert failed["last_error"] == "Health check failed: ConnectionRefusedError"
    assert "10.0.0.5" not in str(failed) and "admin" not in failed["detail"]
    assert failed["checked_at"] and failed["last_error_at"]

    probe.healthy = True
    recovered = _health(client)["cache"]
    assert recovered["status"] == "connected" and recovered["remediation"] is None
    assert recovered["last_error"] == "Health check failed: ConnectionRefusedError"
    assert recovered["last_error_at"] == failed["last_error_at"]
    # History is per workspace.
    assert _health(client, OTHER_WORKSPACE)["cache"]["last_error"] is None


def test_test_connection_is_an_owner_action_and_is_audited(probe: Switch) -> None:
    client = TestClient(main.app)
    denied = client.post("/api/v1/integrations/cache/test", headers=MEMBER)
    assert denied.status_code == 403
    assert probe.calls == 0

    probe.healthy = False
    tested = client.post("/api/v1/integrations/cache/test", headers=OWNER)
    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "degraded"
    assert tested.json()["last_error"].startswith("Health check failed")
    assert client.post("/api/v1/integrations/missing/test", headers=OWNER).status_code == 404

    records = [r for r in governance_store.query_audit(_context()) if r.action == "integration.tested"]
    assert len(records) == 1
    assert (records[0].target, records[0].outcome, records[0].actor) == ("integration:cache", "failed", "owner_int")


def test_turning_an_integration_off_says_how_to_turn_it_back_on(probe: Switch) -> None:
    client = TestClient(main.app)
    saved = client.put("/api/v1/integrations/cache/configuration", headers=OWNER, json={"enabled": False})
    assert saved.status_code == 200
    item = _health(client)["cache"]
    assert (item["status"], item["enabled"], item["available"]) == ("disabled", False, True)
    assert "turn it back on" in item["remediation"]
    assert probe.calls == 0  # an integration turned off is never probed
    actions = [r.action for r in governance_store.query_audit(_context())]
    assert "integration.configured" in actions


def test_workspace_credential_is_encrypted_write_only_and_audited(probe: Switch) -> None:
    client = TestClient(main.app)
    assert client.put("/api/v1/integrations/external-webhook/credential", headers=MEMBER,
                      json={"secret": SECRET}).status_code == 403
    too_short = client.put("/api/v1/integrations/external-webhook/credential", headers=OWNER, json={"secret": "short"})
    assert too_short.status_code == 422
    with_space = client.put("/api/v1/integrations/external-webhook/credential", headers=OWNER,
                            json={"secret": "has a space in it"})
    assert with_space.status_code == 422

    saved = client.put("/api/v1/integrations/external-webhook/credential", headers=OWNER, json={"secret": SECRET})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body == {**body, "integration_id": "external-webhook", "configured": True, "hint": "…abcd", "updated_by": "owner_int"}
    assert SECRET not in saved.text

    stored = main.integration_registry.memory_configurations.get_credential(_context(), "external-webhook")
    assert stored is not None and SECRET not in stored.ciphertext
    item = _health(client)["external-webhook"]
    assert SECRET not in str(item)
    assert item["accepts_workspace_credential"] is True
    assert item["workspace_credential"]["configured"] is True
    assert item["credentials"]["source"] == "workspace"
    assert _health(client, OTHER_WORKSPACE)["external-webhook"]["workspace_credential"]["configured"] is False

    assert client.delete("/api/v1/integrations/external-webhook/credential", headers=OWNER).status_code == 204
    assert client.delete("/api/v1/integrations/external-webhook/credential", headers=OWNER).status_code == 404
    records = [r for r in governance_store.query_audit(_context()) if r.target == "integration:external-webhook"]
    assert [r.action for r in records] == ["integration.credential_set", "integration.credential_removed"]
    assert all(SECRET not in str(dict(r.metadata)) for r in records)


def test_operator_credentials_cannot_be_replaced_by_a_workspace(probe: Switch) -> None:
    client = TestClient(main.app)
    refused = client.put("/api/v1/integrations/cache/credential", headers=OWNER, json={"secret": SECRET})
    assert refused.status_code == 409
    assert "ANUM_VALKEY_URL" in refused.json()["error"]["message"]
    assert client.put("/api/v1/integrations/missing/credential", headers=OWNER, json={"secret": SECRET}).status_code == 404


def test_the_external_webhook_uses_the_workspace_token_and_honours_the_switch() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    configured = settings.model_copy(update={
        "external_webhook_url": "https://hooks.example/actions", "external_webhook_api_key": "operator-token-example",
    })
    registry = IntegrationRegistry([external_webhook_integration(configured)])
    adapter = configured_external_handler(configured, registry)
    assert adapter is not None
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
    call = ToolCall(name="external.action", arguments={"action": "notify"})

    asyncio.run(adapter(call, _context()))
    assert seen[-1].headers["authorization"] == "Bearer operator-token-example"

    registry.set_credential("external-webhook", _context(), SECRET)
    asyncio.run(adapter(call, _context()))
    assert seen[-1].headers["authorization"] == f"Bearer {SECRET}"
    # Another workspace still uses the operator's token.
    asyncio.run(adapter(call, _context("workspace_int_other")))
    assert seen[-1].headers["authorization"] == "Bearer operator-token-example"

    from anum_api.integrations import IntegrationConfiguration

    registry.configure("external-webhook", _context(), IntegrationConfiguration(enabled=False))
    blocked = asyncio.run(adapter(call, _context()))
    assert blocked.status == "blocked" and "turned off" in blocked.summary
    assert len(seen) == 3
    governance_store.clear()


def test_external_webhook_health_shows_only_scheme_and_host() -> None:
    configured = settings.model_copy(update={"external_webhook_url": "https://hooks.example:8443/path?token=abc"})
    definition = external_webhook_integration(configured)
    assert definition.configured and definition.endpoint == "https://hooks.example"
    off = external_webhook_integration(settings.model_copy(update={"external_webhook_url": None}))
    assert not off.configured and "ANUM_EXTERNAL_WEBHOOK_URL" in (off.disabled_detail or "")
    health = asyncio.run(IntegrationRegistry([off]).health())
    assert health[0].status == IntegrationStatus.DISABLED and health[0].available is False
