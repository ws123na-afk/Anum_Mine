"""Tool adapters that reach external systems (REST webhooks, MCP servers).

Responses are untrusted data (threat model T7/G3): bodies are read as a stream and cut
at ``ANUM_TOOL_RESPONSE_MAX_BYTES`` (the result is then marked ``truncated``), the
whole call (connect, send, read) has one overall deadline so a slow, never-ending body
cannot hold a worker, and compressed bodies are refused so a small response cannot
expand past the cap. Tool output is never treated as instructions; when it is put into
a prompt it goes through ``agent_tools.tool_output_prompt_block``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from .agent_tools import ToolCall, ToolHandler, ToolResult
from .schemas import TenantContext
from .settings import Settings

DEFAULT_TOOL_RESPONSE_MAX_BYTES = 256 * 1024
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0


class ToolResponseError(RuntimeError):
    """The integration answered with something ANUM will not accept."""


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


async def read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` of the body; True when more was available."""
    buffer = bytearray()
    # Compressed bodies are refused before this, so these are the bytes on the wire.
    async for chunk in response.aiter_bytes():
        remaining = max_bytes - len(buffer)
        if len(chunk) > remaining:
            buffer.extend(chunk[:remaining])
            return bytes(buffer), True
        buffer.extend(chunk)
    return bytes(buffer), False


def _output_from_body(body: bytes, content_type: str, truncated: bool) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    if truncated or "json" not in content_type.lower():
        return {"text": text}
    try:
        value = json.loads(text)
    except ValueError:
        return {"text": text}
    return value if isinstance(value, dict) else {"data": value}


def cap_output(output: dict[str, Any], max_bytes: int) -> tuple[dict[str, Any], bool]:
    """Bound an already-decoded tool output (MCP) to ``max_bytes`` of JSON."""
    serialized = json.dumps(output, ensure_ascii=False, default=str).encode("utf-8")
    if len(serialized) <= max_bytes:
        return output, False
    return {"text": serialized[:max_bytes].decode("utf-8", errors="replace")}, True


class RestToolAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        allowed_hosts: set[str],
        credential_reference: str | None = None,
        credentials: CredentialProvider | None = None,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = DEFAULT_TOOL_RESPONSE_MAX_BYTES,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        host = urlparse(endpoint).hostname
        if not host or host not in allowed_hosts:
            raise ValueError("REST tool endpoint is outside the integration host allowlist")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self.endpoint = endpoint
        self.target_host = host
        self.credential_reference = credential_reference
        self.credentials = credentials
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        headers = {
            "content-type": "application/json",
            # A compressed body could expand far beyond the cap; ask for none.
            "accept-encoding": "identity",
            "x-anum-tenant-id": context.tenant_id,
            "x-anum-workspace-id": context.workspace_id,
        }
        if self.credential_reference:
            if not self.credentials:
                raise PermissionError("Credential provider is unavailable")
            headers["authorization"] = (
                f"Bearer {self.credentials.resolve(self.credential_reference, context)}"
            )
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        owns_client = self._client is None
        try:
            # One deadline for the whole exchange: httpx timeouts are per operation, so
            # a body that trickles in forever would otherwise never time out.
            async with asyncio.timeout(self.timeout_seconds):
                async with client.stream(
                    "POST", self.endpoint, headers=headers, json=call.arguments
                ) as response:
                    response.raise_for_status()
                    encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if encoding not in {"", "identity"}:
                        raise ToolResponseError("Integration response is compressed; ANUM accepts identity only")
                    body, truncated = await read_capped(response, self.max_response_bytes)
                    content_type = response.headers.get("content-type", "")
                    status_code = response.status_code
        finally:
            if owns_client:
                await client.aclose()
        summary = f"External REST action completed with status {status_code}."
        if truncated:
            summary += f" The response was truncated to {self.max_response_bytes} bytes."
        return ToolResult(
            status="succeeded",
            summary=summary,
            output=_output_from_body(body, content_type, truncated),
            truncated=truncated,
        )


class McpClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class McpToolAdapter:
    def __init__(
        self,
        client: McpClient,
        remote_tool_name: str,
        *,
        max_response_bytes: int = DEFAULT_TOOL_RESPONSE_MAX_BYTES,
        target_host: str | None = None,
    ) -> None:
        self.client = client
        self.remote_tool_name = remote_tool_name
        self.max_response_bytes = max_response_bytes
        self.target_host = target_host

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        arguments = {
            **call.arguments,
            "_anum_context": {
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "actor_id": context.user_id,
            },
        }
        output, truncated = cap_output(
            await self.client.call_tool(self.remote_tool_name, arguments), self.max_response_bytes
        )
        summary = f"MCP tool {self.remote_tool_name} completed."
        if truncated:
            summary += f" The response was truncated to {self.max_response_bytes} bytes."
        return ToolResult(status="succeeded", summary=summary, output=output, truncated=truncated)


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
        max_response_bytes=settings.tool_response_max_bytes,
    )
