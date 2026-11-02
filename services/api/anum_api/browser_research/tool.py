"""The ``web.browse_page`` tool (docs/browser-research.md).

An ordinary registered tool: every call goes through the runtime's mediation (``ToolPolicy``
with roles and governance rules, the emergency stop, autonomy and approvals, run steps,
events and the ``tool.call`` audit record). Low risk and read only, like ``web.fetch_page``,
so it does not ask for approval unless a workspace or organization rule says so; a policy pack
can deny it.

What this handler adds before anything leaves ANUM:

* the address is checked here first (``guard.check_page_url``: https, public, no query);
* **Not available on this server** when the operator has not set up the browser service, or
  the service says Chromium is missing from its image;
* **off per workspace by default**: an owner turns the ``browser-research`` integration on
  under Integrations (``IntegrationRegistry.workspace_enabled``);
* a per-workspace hourly cap (``ANUM_BROWSER_RESEARCH_PER_HOUR``).

The browser itself never runs in this process: the handler sends the address to the browser
service (``service.py``) over the internal network and awaits the answer, so the API's event
loop only waits on a socket. The audit metadata holds the address, the final address, the
status code, counts and the outcome code; never page text, titles or links.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..integration_tools import read_capped
from ..schemas import RiskLevel, TenantContext
from . import BROWSE_TOOL
from .guard import Refused, check_page_url

MAX_SERVICE_RESPONSE_BYTES = 512 * 1024
# Outcomes that are a refusal (nothing was read on purpose) rather than a failure.
BLOCKED_OUTCOMES = frozenset({
    "refused_url", "refused_scheme", "refused_address", "robots_disallowed", "robots_unavailable",
    "login_required", "challenge", "not_text", "workspace_disabled",
})
UNAVAILABLE_TEXT = (
    "Not available on this server: the browser service is not set up "
    "(the operator sets ANUM_BROWSER_RESEARCH_URL and ANUM_BROWSER_RESEARCH_TOKEN; docs/browser-research.md)."
)
DISABLED_TEXT = "Browser research is off for this workspace. A workspace owner turns it on under Integrations, Browser research."

EnabledCheck = Callable[[TenantContext], bool]


class BrowserServiceUrlError(ValueError):
    """``ANUM_BROWSER_RESEARCH_URL`` is not an absolute http(s) URL without credentials."""


def normalize_service_url(value: str) -> str:
    text = value.strip()
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise BrowserServiceUrlError("ANUM_BROWSER_RESEARCH_URL must be an absolute http(s) URL, e.g. http://browser:8090")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise BrowserServiceUrlError("ANUM_BROWSER_RESEARCH_URL must not carry credentials, a query or a fragment")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def service_problem(url: str, token: str) -> str | None:
    """Why the browser service is not set up, or None when it is."""
    if not url.strip() or not token.strip():
        return UNAVAILABLE_TEXT
    try:
        normalize_service_url(url)
    except BrowserServiceUrlError as exc:
        return f"Not available on this server: {exc}."
    if len(token.strip()) < 32:
        return "Not available on this server: ANUM_BROWSER_RESEARCH_TOKEN must be at least 32 characters."
    return None


class HourlyCap:
    """Calls per workspace in the last hour, in this process."""

    def __init__(self, per_hour: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_hour = per_hour
        self._clock = clock
        self._calls: dict[tuple[str, str], deque[float]] = {}

    def take(self, context: TenantContext) -> bool:
        now = self._clock()
        calls = self._calls.setdefault((context.tenant_id, context.workspace_id), deque())
        while calls and now - calls[0] >= 3600:
            calls.popleft()
        if len(calls) >= self.per_hour:
            return False
        calls.append(now)
        return True


class BrowserResearchTool:
    """The handler. Tests pass ``client`` (an ``httpx.AsyncClient`` on a mock transport)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 45.0,
        per_hour: int = 30,
        enabled: EnabledCheck | None = None,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.problem = service_problem(base_url, token)
        self.base_url = normalize_service_url(base_url) if self.problem is None else ""
        self._token = token.strip()
        self.timeout_seconds = timeout_seconds
        self._enabled = enabled
        self._client = client
        self.cap = HourlyCap(per_hour, clock)

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        raw = str(call.arguments.get("url", ""))
        audit: dict[str, Any] = {"tool": BROWSE_TOOL, "url": "", "host": "", "final_url": "", "status_code": None, "requests": 0, "blocked": 0, "bytes": 0}
        try:
            url = check_page_url(raw)
        except Refused as exc:
            try:
                audit["host"] = (urlsplit(raw.strip()).hostname or "")[:253]
            except ValueError:
                pass
            return _done("blocked", str(exc), audit, exc.code)
        audit.update(url=url, host=urlsplit(url).hostname or "")
        if self.problem is not None:
            return _done("failed", self.problem, audit, "unavailable")
        if self._enabled is not None and not await asyncio.to_thread(self._enabled, context):
            return _done("blocked", DISABLED_TEXT, audit, "workspace_disabled")
        if not self.cap.take(context):
            return _done("failed", f"This workspace has read {self.cap.per_hour} pages in the last hour, the most allowed; try again later.", audit, "hourly_cap")
        try:
            status, result = await self._ask(url)
        except (TimeoutError, httpx.TimeoutException):
            return _done("failed", f"The browser service did not answer within {self.timeout_seconds:g} seconds.", audit, "timeout")
        except httpx.HTTPError as exc:
            return _done("failed", f"Not available right now: the browser service could not be reached ({type(exc).__name__}).", audit, "service_unreachable")
        except ValueError:
            return _done("failed", "The browser service sent an answer ANUM could not read.", audit, "bad_response")
        if status == 401:
            return _done("failed", "Not available on this server: the browser service refused ANUM's token (ANUM_BROWSER_RESEARCH_TOKEN).", audit, "service_unauthorised")
        if status == 429:
            return _done("failed", "The browser is busy with another page; try again shortly.", audit, "busy")
        if status == 503:
            message = str(result.get("message") or "Not available on this server: Chromium is not installed in the browser image.")
            return _done("failed", message[:300], audit, "unavailable")
        if status != 200:
            return _done("failed", f"The browser service answered HTTP {status}.", audit, "service_error")
        return _from_service(result, audit)

    async def _ask(self, url: str) -> tuple[int, dict[str, Any]]:
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False, trust_env=False)
        owns = self._client is None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with client.stream(
                    "POST", f"{self.base_url}/v1/browse",
                    content=json.dumps({"url": url}).encode(),
                    headers={"authorization": f"Bearer {self._token}", "content-type": "application/json", "accept-encoding": "identity"},
                ) as response:
                    body, truncated = await read_capped(response, MAX_SERVICE_RESPONSE_BYTES)
                    if truncated:
                        raise ValueError("browser service answer too large")
                    status = response.status_code
        finally:
            if owns:
                await client.aclose()
        try:
            value = json.loads(body.decode("utf-8")) if body else {}
        except UnicodeDecodeError:
            raise ValueError("not utf-8") from None
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return status, value


def _done(status: str, summary: str, audit: dict[str, Any], outcome: str) -> ToolResult:
    return ToolResult(status=status, summary=summary, audit_metadata={**audit, "outcome": outcome})


def _from_service(result: dict[str, Any], audit: dict[str, Any]) -> ToolResult:
    outcome = str(result.get("outcome") or "bad_response")[:40]
    final_url = str(result.get("final_url") or "")[:2048]
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    status_code = result.get("status_code")
    audit.update(
        final_url=final_url,
        final_host=(urlsplit(final_url).hostname or "") if final_url else "",
        status_code=status_code if isinstance(status_code, int) else None,
        requests=_count(stats.get("requests")),
        blocked=_count(stats.get("blocked")),
        bytes=_count(stats.get("bytes")),
        blocked_reasons={str(k)[:40]: _count(v) for k, v in list((stats.get("blocked_reasons") or {}).items())[:20]} if isinstance(stats.get("blocked_reasons"), dict) else {},
    )
    if outcome != "succeeded":
        status = "blocked" if outcome in BLOCKED_OUTCOMES else "failed"
        return _done(status, str(result.get("message") or "The page was not read.")[:300], audit, outcome)
    title = " ".join(str(result.get("title") or "").split())[:300]
    text = str(result.get("text") or "")[:20_000]
    links = []
    for item in result.get("links") or []:
        if isinstance(item, dict) and str(item.get("url", "")).startswith("https://"):
            links.append({"url": str(item["url"])[:2048], "text": " ".join(str(item.get("text") or "").split())[:200]})
        if len(links) >= 40:
            break
    audit.update(link_count=len(links), text_chars=len(text))
    shown = title or final_url
    return ToolResult(
        status="succeeded",
        summary=f"Read {shown} ({final_url}).",
        output={
            "url": audit["url"], "final_url": final_url, "status_code": audit["status_code"], "title": title,
            "text": text, "truncated": bool(result.get("truncated")), "links": links,
        },
        audit_metadata={**audit, "outcome": "succeeded"},
    )


def _count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def browse_definition(timeout_seconds: float = 45.0) -> ToolDefinition:
    return ToolDefinition(
        name=BROWSE_TOOL,
        description=(
            "Open one public https web page in a sandboxed, read-only browser and read its text and links "
            "(runs the page's scripts; obeys robots.txt; no logins, forms, downloads or query strings). Arguments: url."
        ),
        risk_level=RiskLevel.LOW,
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=min(300, int(timeout_seconds) + 15),
        idempotent=True,
        category="general",
        action_kind="read",
        input_schema={"type": "object", "properties": {"url": {"type": "string", "description": "An https URL without a query string."}}, "required": ["url"]},
    )


def configured_browser_handler(settings: Any, enabled: EnabledCheck | None = None) -> BrowserResearchTool:
    """The handler for this deployment; unset or invalid settings make it answer "Not available"."""
    return BrowserResearchTool(
        base_url=settings.browser_research_url,
        token=settings.browser_research_token,
        timeout_seconds=settings.browser_research_timeout_seconds,
        per_hour=settings.browser_research_per_hour,
        enabled=enabled,
    )


def register_browser_tools(registry: ToolRegistry, settings: Any, enabled: EnabledCheck | None = None) -> ToolRegistry:
    """Add ``web.browse_page`` to a runtime's registry (the API's and the Temporal worker's).

    Always registered, so a request for it gets an honest "Not available on this server"
    instead of a different tool, when the browser service is not set up."""
    if BROWSE_TOOL not in registry.names:
        registry.register(browse_definition(settings.browser_research_timeout_seconds), configured_browser_handler(settings, enabled))
    return registry
