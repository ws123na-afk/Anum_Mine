"""A mediated adapter for MCP-style tool servers.

This implements the MEDIATION PATTERN the docs call for: ANUM discovers and
invokes MCP tools through a minimal JSON-RPC 2.0 subset -- `tools/list` and
`tools/call`, the same method names the real Model Context Protocol uses --
over an injectable transport. `McpAdapter.discover_tools()` maps a server's
tool listing into ordinary `tools.ToolContract` instances, and
`McpAdapter.build_handler()` returns an ordinary `tools.ToolHandler`. Both
therefore register into a `tools.ToolRegistry` and run through
`tools.execute_tool()` exactly like an internal tool or a REST integration,
so tenant policy, risk-based approval, and audit logging all still apply to
MCP-sourced tools -- ANUM never lets an MCP server bypass mediation.

Scope note (read this before assuming more is implemented): this is
deliberately NOT a full MCP client. It does not implement MCP's transport
negotiation (stdio / SSE / streamable-HTTP), capability negotiation,
`initialize` handshake, resources, prompts, sampling, or notifications --
only the `tools/list` / `tools/call` request and response shapes, which is
enough surface to demonstrate and test the mediation pattern the "Now" scope
asks for. A production MCP client would replace the transport with a real
one (stdio subprocess, SSE/HTTP session, ...) and would need to add the
missing protocol surface on top of this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from .schemas import RiskLevel
from .tools import (
    RetryPolicy,
    ToolContract,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolResultStatus,
)


# An in-process stand-in for a real MCP server's JSON-RPC handler: given one
# decoded JSON-RPC 2.0 request object, return the decoded JSON-RPC 2.0
# response object. A production transport would instead serialize the
# request onto stdio/SSE/HTTP and decode the reply.
McpTransport: TypeAlias = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class McpProtocolError(RuntimeError):
    """Raised when an MCP JSON-RPC response is malformed or reports an error."""


class McpAdapter:
    """Discovers and invokes tools on one MCP-style server via JSON-RPC."""

    def __init__(
        self,
        transport: McpTransport,
        *,
        default_risk_level: RiskLevel = RiskLevel.MEDIUM,
        default_timeout_seconds: float = 30.0,
        default_retry_policy: RetryPolicy | None = None,
        scopes_for_tool: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._transport = transport
        self._default_risk_level = default_risk_level
        self._default_timeout_seconds = default_timeout_seconds
        self._default_retry_policy = default_retry_policy or RetryPolicy()
        self._scopes_for_tool = scopes_for_tool or (lambda name: [f"mcp:{name}"])
        self._next_request_id = 0

    async def discover_tools(self) -> list[ToolContract]:
        """Call `tools/list` and map the server's tools into `ToolContract`s.

        Risk level, timeout, retry policy, and required scopes are not part
        of MCP's `tools/list` shape, so they come from this adapter's
        defaults (or `scopes_for_tool`) rather than the server -- an MCP
        server describes *what* a tool does, not how much ANUM should trust
        it, and that trust decision stays under ANUM's policy control.
        """

        result = await self._call("tools/list", {})
        raw_tools = result.get("tools", [])
        contracts: list[ToolContract] = []
        for raw_tool in raw_tools:
            name = raw_tool["name"]
            contracts.append(
                ToolContract(
                    name=name,
                    description=raw_tool.get("description") or "",
                    input_schema=raw_tool.get("inputSchema") or {},
                    output_schema=raw_tool.get("outputSchema") or {},
                    required_scopes=self._scopes_for_tool(name),
                    risk_level=self._default_risk_level,
                    timeout_seconds=self._default_timeout_seconds,
                    retry_policy=self._default_retry_policy,
                    idempotent=False,
                )
            )
        return contracts

    def build_handler(self, tool_name: str) -> ToolHandler:
        """Return a `ToolHandler` that calls `tools/call` for `tool_name`.

        The returned handler is a plain async callable, so it registers into
        a `tools.ToolRegistry` next to internal and REST-integration
        handlers and is invoked through the same `tools.execute_tool()`
        mediation path.
        """

        async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
            try:
                result = await self._call("tools/call", {"name": tool_name, "arguments": inputs})
            except McpProtocolError as exc:
                return ToolResult(status=ToolResultStatus.RECOVERABLE_FAILURE, error_message=str(exc))

            is_error = bool(result.get("isError", False))
            structured = result.get("structuredContent")
            output = structured if isinstance(structured, dict) else _flatten_content(
                result.get("content") or []
            )

            if is_error:
                return ToolResult(
                    status=ToolResultStatus.RECOVERABLE_FAILURE,
                    error_message=output.get("text") or f"MCP tool '{tool_name}' reported an error",
                    partial_output=output or None,
                )
            return ToolResult(status=ToolResultStatus.SUCCESS, output=output)

        return handler

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id,
            "method": method,
            "params": params,
        }
        response = await self._transport(request)

        if response.get("jsonrpc") != "2.0":
            raise McpProtocolError("malformed MCP response: missing/invalid jsonrpc version")
        if response.get("id") != request["id"]:
            raise McpProtocolError("malformed MCP response: id does not match the request")
        if "error" in response:
            error = response["error"]
            raise McpProtocolError(
                f"MCP server error {error.get('code')}: {error.get('message')}"
            )
        if "result" not in response:
            raise McpProtocolError("malformed MCP response: missing 'result'")
        return response["result"]


def _flatten_content(content: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse an MCP `content` array of `{"type": "text", "text": ...}` blocks.

    Only the `text` content type is handled -- this adapter's minimal-subset
    scope does not cover image/audio/embedded-resource content blocks.
    """

    texts = [str(item.get("text", "")) for item in content if item.get("type") == "text"]
    return {"text": "\n".join(texts)} if texts else {}
