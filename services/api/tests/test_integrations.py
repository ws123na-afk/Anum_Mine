import asyncio

import httpx

from anum_api.agent_tools import ToolCall
from anum_api.integration_tools import McpToolAdapter, RestToolAdapter
from anum_api.integrations import (
    CredentialMetadata,
    IntegrationDefinition,
    IntegrationKind,
    IntegrationRegistry,
    IntegrationStatus,
)
from anum_api.schemas import TenantContext


def context() -> TenantContext:
    return TenantContext(
        tenant_id="tenant_a",
        workspace_id="workspace_a",
        user_id="user_a",
        roles=["owner"],
    )


def test_integration_registry_reports_connected_and_degraded_without_secrets() -> None:
    async def healthy() -> None:
        return None

    async def unhealthy() -> None:
        raise ConnectionError("secret connection detail")

    registry = IntegrationRegistry(
        [
            IntegrationDefinition(
                id="healthy",
                name="Healthy",
                kind=IntegrationKind.EVENT_BUS,
                endpoint="nats://localhost:4222",
                credentials=CredentialMetadata(configured=True),
                probe=healthy,
            ),
            IntegrationDefinition(
                id="unhealthy",
                name="Unhealthy",
                kind=IntegrationKind.CACHE,
                endpoint="redis://localhost:6379",
                credentials=CredentialMetadata(configured=True),
                probe=unhealthy,
            ),
        ]
    )

    results = asyncio.run(registry.health())

    assert results[0].status == IntegrationStatus.CONNECTED
    assert results[1].status == IntegrationStatus.DEGRADED
    assert "secret connection detail" not in results[1].detail


def test_rest_tool_adapter_enforces_host_allowlist() -> None:
    try:
        RestToolAdapter(endpoint="https://blocked.example/hook", allowed_hosts={"allowed.example"})
    except ValueError as exc:
        assert "allowlist" in str(exc)
    else:
        raise AssertionError("unapproved REST host should be rejected")


def test_rest_tool_adapter_sends_tenant_context_and_returns_normalized_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-anum-tenant-id"] == "tenant_a"
        assert request.headers["x-anum-workspace-id"] == "workspace_a"
        return httpx.Response(200, json={"delivery_id": "delivery_1"})

    adapter = RestToolAdapter(
        endpoint="https://hooks.example/actions",
        allowed_hosts={"hooks.example"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = asyncio.run(adapter(ToolCall(name="external.action", arguments={"text": "go"}), context()))

    assert result.status == "succeeded"
    assert result.output == {"delivery_id": "delivery_1"}


def test_mcp_adapter_propagates_scoped_context() -> None:
    class FakeMcpClient:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            assert name == "documents.publish"
            assert arguments["_anum_context"]["tenant_id"] == "tenant_a"
            return {"published": True}

    adapter = McpToolAdapter(FakeMcpClient(), "documents.publish")

    result = asyncio.run(adapter(ToolCall(name="mcp.publish", arguments={"id": "doc_1"}), context()))

    assert result.output == {"published": True}
