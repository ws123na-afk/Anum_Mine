"""The general pattern for wrapping an external REST API as a mediated tool.

`RestToolAdapter` is generic: give it a base URL, an `httpx.AsyncClient`, a
function that turns tool inputs into an HTTP request, and a function that
maps the JSON response into the tool's output shape, and its instances are
themselves valid `tools.ToolHandler`s -- callable `(inputs, context) ->
ToolResult` -- so they register into a `tools.ToolRegistry` and run through
`tools.execute_tool()` exactly like any other tool.

The one concrete example wired up here, `lookup_github_repo`, calls a REST
shape like GitHub's `GET /repos/{owner}/{repo}`. It is exercised purely
against `httpx.MockTransport` in tests -- no live network call is made or
required. Going to production is a one-line swap: construct the
`httpx.AsyncClient` with a real transport (or no transport override at all)
instead of a `MockTransport`, and point `base_url` at the real API.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

import httpx

from .schemas import RiskLevel
from .tools import (
    RetryPolicy,
    ToolContract,
    ToolExecutionContext,
    ToolHandler,
    ToolRegistry,
    ToolResult,
    ToolResultStatus,
)


@dataclass(frozen=True, slots=True)
class RestRequest:
    """One HTTP request, as derived from a tool call's inputs."""

    method: str
    path: str
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None


RequestBuilder: TypeAlias = Callable[[dict[str, Any], ToolExecutionContext], RestRequest]
ResponseMapper: TypeAlias = Callable[[int, Any], dict[str, Any]]


class RestToolAdapter:
    """A `ToolHandler` that mediates one REST API call.

    `build_request` turns tool inputs into a `RestRequest`; `map_response`
    turns the raw decoded JSON body into the tool's `output` dict per its
    `output_schema`. Both are plain functions so the same adapter class
    covers any REST-shaped tool -- only the two functions and the contract
    change per integration.
    """

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient,
        build_request: RequestBuilder,
        map_response: ResponseMapper,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._build_request = build_request
        self._map_response = map_response

    async def __call__(self, inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        try:
            request = self._build_request(inputs, context)
        except Exception as exc:  # noqa: BLE001 - bad tool input, not a bug
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"invalid tool input: {exc}",
            )

        url = f"{self._base_url}{request.path}"
        try:
            response = await self._client.request(
                request.method,
                url,
                params=request.params,
                json=request.json_body,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"request to {url} failed: {exc}",
            )

        if response.status_code >= 500:
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"upstream returned {response.status_code}",
            )
        if response.status_code >= 400:
            return ToolResult(
                status=ToolResultStatus.BLOCKED,
                error_message=f"request rejected by upstream: {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"upstream response was not valid JSON: {exc}",
            )

        try:
            output = self._map_response(response.status_code, payload)
        except Exception as exc:  # noqa: BLE001 - unexpected upstream shape, not a bug
            return ToolResult(
                status=ToolResultStatus.RECOVERABLE_FAILURE,
                error_message=f"failed to map upstream response: {exc}",
            )

        return ToolResult(status=ToolResultStatus.SUCCESS, output=output)


# --- one concrete example integration: GitHub repo lookup -----------------


GITHUB_LOOKUP_REPO_CONTRACT = ToolContract(
    name="lookup_github_repo",
    description="Look up a GitHub repository's public metadata (GET /repos/{owner}/{repo}).",
    input_schema={
        "type": "object",
        "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
        "required": ["owner", "repo"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "full_name": {"type": "string"},
            "default_branch": {"type": "string"},
            "stargazers_count": {"type": "integer"},
            "private": {"type": "boolean"},
        },
    },
    required_scopes=["integration:github:read"],
    risk_level=RiskLevel.LOW,
    timeout_seconds=10.0,
    retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.2),
    idempotent=True,
)


def _build_github_repo_request(
    inputs: dict[str, Any], context: ToolExecutionContext
) -> RestRequest:
    owner = inputs["owner"]
    repo = inputs["repo"]
    return RestRequest(method="GET", path=f"/repos/{owner}/{repo}")


def _map_github_repo_response(status_code: int, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object in the GitHub repo response")
    return {
        "full_name": payload["full_name"],
        "default_branch": payload.get("default_branch", "main"),
        "stargazers_count": payload.get("stargazers_count", 0),
        "private": payload.get("private", False),
    }


def build_lookup_github_repo_tool(
    client: httpx.AsyncClient, *, base_url: str = "https://api.github.com"
) -> tuple[ToolContract, ToolHandler]:
    """Build the (contract, handler) pair for the GitHub repo lookup tool.

    `client` is injected so tests can supply one built with
    `httpx.MockTransport` -- swap in a client pointed at the real network to
    go to production.
    """

    adapter = RestToolAdapter(
        base_url=base_url,
        client=client,
        build_request=_build_github_repo_request,
        map_response=_map_github_repo_response,
    )
    return GITHUB_LOOKUP_REPO_CONTRACT, adapter


def build_rest_integration_registry(client: httpx.AsyncClient) -> ToolRegistry:
    """Convenience factory: one `ToolRegistry` with the GitHub lookup tool registered."""

    registry = ToolRegistry()
    contract, handler = build_lookup_github_repo_tool(client)
    registry.register(contract, handler)
    return registry
