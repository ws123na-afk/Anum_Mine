"""Private web search: the ``web_search`` tool, backed by a self-hosted SearXNG.

The tool is an ordinary registered tool, so every call goes through the runtime's
mediation (tool policy, governance rules, run steps, events). It is read-only and low
risk: no approval unless a workspace or organization rule asks for one.

Rules this module keeps (docs/tools-and-integrations.md#web-search):

* The SearXNG address is operator configuration (``ANUM_SEARXNG_URL``) only. Nothing a
  tenant sends (tool arguments, a saved integration endpoint) can change where the
  request goes, so the tool cannot be turned into a request forger (SSRF).
* One overall deadline for the exchange, a byte cap on the response, identity encoding
  only, and at most ``ANUM_WEB_SEARCH_MAX_RESULTS`` results.
* Only ``http`` and ``https`` result links are returned; anything else is dropped.
* Every call is audited with the query *length* and the result count, never the query
  text or the results.
* Results are untrusted data (threat model G3): when they reach a prompt they go through
  ``agent_tools.tool_output_prompt_block``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .agent_tools import ToolCall, ToolResult
from .integration_tools import DEFAULT_TOOL_RESPONSE_MAX_BYTES, read_capped
from .schemas import TenantContext
from .settings import Settings

WEB_SEARCH_TOOL = "web_search"
SEARXNG_INTEGRATION_ID = "searxng"
MAX_QUERY_CHARS = 400
MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 600
MAX_URL_CHARS = 2048
ALLOWED_RESULT_SCHEMES = frozenset({"http", "https"})

# Called with the workspace context; False means the workspace switched the tool off.
EnabledCheck = Callable[[TenantContext], bool]


class SearxngUrlError(ValueError):
    """``ANUM_SEARXNG_URL`` is not an absolute http(s) URL without credentials."""


def normalize_searxng_url(value: str) -> str:
    """The base URL to call, or raise :class:`SearxngUrlError` explaining what is wrong."""
    text = value.strip()
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise SearxngUrlError("ANUM_SEARXNG_URL must be an absolute http(s) URL, e.g. http://searxng:8080")
    if parts.username or parts.password:
        raise SearxngUrlError("ANUM_SEARXNG_URL must not carry credentials")
    if parts.query or parts.fragment:
        raise SearxngUrlError("ANUM_SEARXNG_URL must not carry a query or fragment")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _safe_result_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_URL_CHARS or any(ch.isspace() or ord(ch) < 32 for ch in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.lower() not in ALLOWED_RESULT_SCHEMES or not parts.hostname:
        return None
    return text


def parse_results(payload: Any, max_results: int) -> tuple[list[dict[str, str]], int]:
    """Title/url/snippet for at most ``max_results`` http(s) results, and how many were dropped."""
    raw = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise ValueError("SearXNG answered without a results list")
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    dropped = 0
    for item in raw:
        if len(results) >= max_results:
            break
        if not isinstance(item, dict):
            dropped += 1
            continue
        url = _safe_result_url(item.get("url"))
        if url is None:
            dropped += 1
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "title": _clean_text(item.get("title"), MAX_TITLE_CHARS) or url,
                "url": url,
                "snippet": _clean_text(item.get("content"), MAX_SNIPPET_CHARS),
            }
        )
    return results, dropped


def _summary(results: list[dict[str, str]]) -> str:
    if not results:
        return "Web search returned no results."
    lines = [f"Found {len(results)} web result{'s' if len(results) != 1 else ''}:"]
    lines.extend(f"{index}. {item['title']} - {item['url']}" for index, item in enumerate(results, 1))
    return "\n".join(lines)


class SearxngWebSearch:
    """The ``web_search`` tool handler: GET ``<base>/search?format=json`` on SearXNG."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 8.0,
        max_results: int = 5,
        safe_search: int = 1,
        max_response_bytes: int = DEFAULT_TOOL_RESPONSE_MAX_BYTES,
        enabled: EnabledCheck | None = None,
    ) -> None:
        if max_results < 1:
            raise ValueError("max_results must be positive")
        if safe_search not in {0, 1, 2}:
            raise ValueError("safe_search must be 0, 1 or 2")
        self.base_url = normalize_searxng_url(base_url)
        # Host only, as approvals and governance rules see it (threat model G2).
        self.target_host = urlsplit(self.base_url).hostname
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results
        self.safe_search = safe_search
        self.max_response_bytes = max_response_bytes
        self._client = client
        self._enabled = enabled

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        query = " ".join(str(call.arguments.get("query", "")).split())
        audit: dict[str, Any] = {
            "tool": WEB_SEARCH_TOOL,
            "target": self.target_host,
            "query_length": len(query),
            "result_count": 0,
        }
        if not query:
            return self._failed("blocked", "Web search needs a non-empty query.", audit, "empty_query")
        if len(query) > MAX_QUERY_CHARS:
            return self._failed(
                "blocked",
                f"Web search queries are limited to {MAX_QUERY_CHARS} characters.",
                audit,
                "query_too_long",
            )
        if self._enabled is not None and not await asyncio.to_thread(self._enabled, context):
            return self._failed(
                "blocked",
                "Web search is turned off for this workspace (Integrations > SearXNG web search).",
                audit,
                "workspace_disabled",
            )
        try:
            payload = await self._search(query)
            results, dropped = parse_results(payload, self.max_results)
        except TimeoutError:
            return self._failed(
                "failed", f"Web search timed out after {self.timeout_seconds:g} seconds.", audit, "timeout"
            )
        except httpx.TimeoutException:
            return self._failed(
                "failed", f"Web search timed out after {self.timeout_seconds:g} seconds.", audit, "timeout"
            )
        except httpx.HTTPStatusError as exc:
            return self._failed(
                "failed",
                f"The search service answered HTTP {exc.response.status_code}.",
                audit,
                "http_error",
            )
        except httpx.HTTPError as exc:
            return self._failed(
                "failed", f"The search service is unreachable ({type(exc).__name__}).", audit, "unreachable"
            )
        except ValueError:
            return self._failed(
                "failed", "The search service returned a response ANUM could not read.", audit, "bad_response"
            )
        audit.update(result_count=len(results), dropped_results=dropped, outcome="succeeded")
        return ToolResult(
            status="succeeded",
            summary=_summary(results),
            output={"query": query, "results": results},
            audit_metadata=audit,
        )

    async def _search(self, query: str) -> Any:
        params = {
            "q": query,
            "format": "json",
            "safesearch": str(self.safe_search),
            "categories": "general",
        }
        headers = {"accept": "application/json", "accept-encoding": "identity"}
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False)
        owns_client = self._client is None
        try:
            # One deadline for connect, send and read (httpx timeouts are per operation).
            async with asyncio.timeout(self.timeout_seconds):
                async with client.stream(
                    "GET", f"{self.base_url}/search", params=params, headers=headers
                ) as response:
                    response.raise_for_status()
                    encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if encoding not in {"", "identity"}:
                        raise ValueError("compressed response")
                    body, truncated = await read_capped(response, self.max_response_bytes)
        finally:
            if owns_client:
                await client.aclose()
        if truncated:
            raise ValueError("response larger than the tool response cap")
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _failed(status: str, summary: str, audit: dict[str, Any], outcome: str) -> ToolResult:
        return ToolResult(status=status, summary=summary, audit_metadata={**audit, "outcome": outcome})


def configured_web_search_handler(
    settings: Settings, enabled: EnabledCheck | None = None
) -> SearxngWebSearch | None:
    """The handler for ``ANUM_SEARXNG_URL``, or None when web search is off or misconfigured.

    A malformed URL does not stop the API: the tool stays unregistered and the
    integration health explains what to fix.
    """
    if not settings.searxng_url.strip():
        return None
    try:
        return SearxngWebSearch(
            base_url=settings.searxng_url,
            timeout_seconds=settings.web_search_timeout_seconds,
            max_results=settings.web_search_max_results,
            safe_search=settings.web_search_safe_search,
            max_response_bytes=settings.tool_response_max_bytes,
            enabled=enabled,
        )
    except SearxngUrlError:
        return None
