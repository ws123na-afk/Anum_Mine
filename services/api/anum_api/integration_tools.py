from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from .agent_tools import ToolCall, ToolHandler, ToolResult
from .schemas import TenantContext
from .settings import Settings


class CredentialProvider(Protocol):
    def resolve(self, reference: str, context: TenantContext) -> str: ...


class EnvironmentCredentialProvider:
    def __init__(self, values: dict[str, str | None]) -> None:
        self._values = values

    def resolve(self, reference: str, _: TenantContext) -> str:
        value = self._values.get(reference)
        if not value:
            raise PermissionError(f"Credential is not configured: {reference}")
        return value


class RestToolAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        allowed_hosts: set[str],
        credential_reference: str | None = None,
        credentials: CredentialProvider | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        host = urlparse(endpoint).hostname
        if not host or host not in allowed_hosts:
            raise ValueError("REST tool endpoint is outside the integration host allowlist")
        self.endpoint = endpoint
        self.credential_reference = credential_reference
        self.credentials = credentials
        self._client = client

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        headers = {
            "content-type": "application/json",
            "x-anum-tenant-id": context.tenant_id,
            "x-anum-workspace-id": context.workspace_id,
        }
        if self.credential_reference:
            if not self.credentials:
                raise PermissionError("Credential provider is unavailable")
            headers["authorization"] = (
                f"Bearer {self.credentials.resolve(self.credential_reference, context)}"
            )
        client = self._client or httpx.AsyncClient(timeout=30)
        owns_client = self._client is None
        try:
            response = await client.post(self.endpoint, headers=headers, json=call.arguments)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            output: dict[str, Any] = response.json() if "json" in content_type else {"text": response.text}
            return ToolResult(
                status="succeeded",
                summary=f"External REST action completed with status {response.status_code}.",
                output=output,
            )
        finally:
            if owns_client:
                await client.aclose()


class McpClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class McpToolAdapter:
    def __init__(self, client: McpClient, remote_tool_name: str) -> None:
        self.client = client
        self.remote_tool_name = remote_tool_name

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        arguments = {
            **call.arguments,
            "_anum_context": {
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "actor_id": context.user_id,
            },
        }
        output = await self.client.call_tool(self.remote_tool_name, arguments)
        return ToolResult(
            status="succeeded",
            summary=f"MCP tool {self.remote_tool_name} completed.",
            output=output,
        )


def configured_external_handler(settings: Settings) -> ToolHandler | None:
    if not settings.external_webhook_url:
        return None
    host = urlparse(settings.external_webhook_url).hostname
    if not host:
        raise ValueError("ANUM_EXTERNAL_WEBHOOK_URL must be an absolute URL")
    provider = EnvironmentCredentialProvider(
        {"external-webhook": settings.external_webhook_api_key}
    )
    return RestToolAdapter(
        endpoint=settings.external_webhook_url,
        allowed_hosts={host},
        credential_reference="external-webhook" if settings.external_webhook_api_key else None,
        credentials=provider,
    )
