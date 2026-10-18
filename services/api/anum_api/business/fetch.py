"""The read-only ``web.fetch_page`` tool (docs/business.md#page-fetch).

The small native page fetcher the reuse register asked for instead of a crawler. It is an
ordinary registered tool, so every call goes through the runtime's mediation (tool policy,
governance rules, audit). Rules it keeps:

* **SSRF guard**: the URL and every redirect hop go through ``model_egress.check_endpoint`` and
  are dialled through ``PinnedEgressTransport`` (resolved once, every address checked, no DNS
  rebinding), the guard ``commerce/http.py`` uses for owner-supplied URLs. Unlike that module
  the guard is always on, in every environment: research pages are on the public internet,
  so private, loopback and link-local addresses are never read. HTTPS on the default port only.
* **robots.txt**: read before any page of a host (RFC 9309: a 4xx answer means no rules, a 5xx
  answer or no answer means nothing may be read) and obeyed for ``ANUM-Research`` and ``*``.
  ``Crawl-delay`` (up to 10 seconds) sets the wait between requests to that host.
* **Rate limits**: at most one request per host per second (or its crawl delay), process-wide.
* **Limits**: one overall deadline, a byte cap, text and XML content types only, at most three
  redirects, no cookies, no credentials, no query strings (a research page never needs one, and
  it removes the easy channel for leaking data in a URL).
* **No logins or captchas**: a 401 or 403, a password field or a known challenge page is reported
  as "behind a login or a captcha" and nothing of it is returned.
* **Untrusted output**: the page text is data. Callers keep it labelled (``prompt_provenance``);
  the audit record carries the host, status and size only, never the content.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..agent_tools import ToolCall, ToolDefinition, ToolResult
from ..model_egress import EgressPolicy, PinnedEgressTransport, UnsafeModelEndpointError, check_endpoint
from ..schemas import RiskLevel, TenantContext

FETCH_TOOL = "web.fetch_page"
USER_AGENT = "ANUM-Research/1.0 (+https://anum.example/research; reads public pages, obeys robots.txt)"
ROBOTS_AGENT = "ANUM-Research"
MAX_BYTES = 1_500_000
MAX_HTML_CHARS = 300_000
MAX_TEXT_CHARS = 20_000
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 10.0
MIN_INTERVAL_SECONDS = 1.0
MAX_CRAWL_DELAY = 10.0
ROBOTS_TTL_SECONDS = 3600.0
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")
CHALLENGE = re.compile(
    r"g-recaptcha|hcaptcha|cf-challenge|challenge-platform|cf_chl_|just a moment\.\.\.|attention required! \| cloudflare|"
    r"<input[^>]+type=[\"']?password",
    re.IGNORECASE,
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass
class _Robots:
    parser: RobotFileParser | None  # None: nothing may be read (5xx or unreachable)
    fetched_at: float
    text: str = ""


class PageFetcher:
    """The handler. Tests pass ``inner`` (an ``httpx.MockTransport``) and ``resolve`` (a fake DNS)."""

    def __init__(
        self,
        *,
        inner: httpx.AsyncBaseTransport | None = None,
        resolve: Resolver | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = MIN_INTERVAL_SECONDS,
        timeout_seconds: float = TIMEOUT_SECONDS,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self.policy = EgressPolicy(enforce=True)
        self._inner = inner
        self._resolve = resolve
        self._sleep = sleep
        self._clock = clock
        self.min_interval = min_interval
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._robots: dict[str, _Robots] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def reset(self) -> None:
        self._robots.clear()
        self._last.clear()
        self._locks.clear()

    # -- plumbing ----------------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        transport = PinnedEgressTransport(self.policy, resolve=self._resolve, inner=self._inner)
        return httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False, timeout=self.timeout_seconds)

    async def _pace(self, host: str, delay: float) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, -1e9) + delay - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._last[host] = self._clock()

    async def _get(self, url: str, delay: float) -> tuple[int, dict[str, str], bytes, bool]:
        host = urlsplit(url).hostname or ""
        await self._pace(host, delay)
        headers = {"user-agent": USER_AGENT, "accept": "text/html,application/xhtml+xml,text/plain,application/xml;q=0.9", "accept-encoding": "identity"}
        async with self._client() as client:
            async with asyncio.timeout(self.timeout_seconds):
                async with client.stream("GET", url, headers=headers) as response:
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            truncated = True
                            break
                        chunks.append(chunk)
                    return response.status_code, {k.lower(): v for k, v in response.headers.items()}, b"".join(chunks), truncated

    async def robots(self, scheme: str, host: str) -> _Robots:
        cached = self._robots.get(host)
        if cached is not None and self._clock() - cached.fetched_at < ROBOTS_TTL_SECONDS:
            return cached
        url = f"{scheme}://{host}/robots.txt"
        try:
            status, _, body, _ = await self._get(url, self.min_interval)
        except (httpx.HTTPError, TimeoutError):
            entry = _Robots(parser=None, fetched_at=self._clock())
        else:
            if 200 <= status < 300:
                text = body[:500_000].decode("utf-8", errors="replace")
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                entry = _Robots(parser=parser, fetched_at=self._clock(), text=text[:20_000])
            elif 400 <= status < 500:
                parser = RobotFileParser()
                parser.parse([])
                entry = _Robots(parser=parser, fetched_at=self._clock())
            else:
                entry = _Robots(parser=None, fetched_at=self._clock())
        self._robots[host] = entry
        return entry

    def _delay(self, robots: _Robots) -> float:
        if robots.parser is None:
            return self.min_interval
        delay = robots.parser.crawl_delay(ROBOTS_AGENT) or robots.parser.crawl_delay("*")
        return min(MAX_CRAWL_DELAY, max(self.min_interval, float(delay or 0)))

    # -- the tool ---------------------------------------------------------------------------------

    @staticmethod
    def _refuse(status: str, summary: str, audit: dict[str, Any], outcome: str) -> ToolResult:
        return ToolResult(status=status, summary=summary, audit_metadata={**audit, "outcome": outcome})

    def _check(self, url: str) -> str:
        """The URL to read, or raise ``ValueError`` with a reason a person can act on."""
        text = (url or "").strip()
        if not text or len(text) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in text):
            raise ValueError("The page address is empty, too long or has spaces.")
        parts = urlsplit(text)
        if parts.scheme.lower() != "https":
            raise ValueError("Only https pages are read.")
        if parts.username or parts.password:
            raise ValueError("Page addresses with a user name or password are not read.")
        if parts.query:
            raise ValueError("Page addresses with a query string are not read.")
        try:
            check_endpoint(text, self.policy)
        except UnsafeModelEndpointError:
            raise ValueError("That address is not a public internet site, so it is not read.") from None
        return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", "", ""))

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        raw = str(call.arguments.get("url", ""))
        audit: dict[str, Any] = {"tool": FETCH_TOOL, "host": (urlsplit(raw).hostname or "")[:253], "bytes": 0, "status_code": None}
        try:
            url = self._check(raw)
        except ValueError as exc:
            return self._refuse("blocked", str(exc), audit, "refused_url")
        try:
            for _ in range(MAX_REDIRECTS + 1):
                parts = urlsplit(url)
                host = parts.hostname or ""
                audit["host"] = host
                robots = await self.robots("https", host)
                if robots.parser is None:
                    return self._refuse("blocked", f"{host} did not answer for robots.txt, so none of its pages are read.", audit, "robots_unavailable")
                path_is_robots = parts.path == "/robots.txt"
                if not path_is_robots and not robots.parser.can_fetch(ROBOTS_AGENT, url):
                    return self._refuse("blocked", f"robots.txt on {host} does not allow reading {parts.path or '/'}.", audit, "robots_disallowed")
                if path_is_robots:
                    body_text = robots.text
                    return ToolResult(
                        status="succeeded", summary=f"Read robots.txt on {host}.",
                        output={"url": url, "final_url": url, "status_code": 200, "content_type": "text/plain", "html": body_text, "text": body_text[:MAX_TEXT_CHARS]},
                        audit_metadata={**audit, "status_code": 200, "bytes": len(body_text), "outcome": "succeeded"},
                    )
                status, headers, body, truncated = await self._get(url, self._delay(robots))
                audit.update(status_code=status, bytes=len(body))
                if status in (301, 302, 303, 307, 308):
                    location = headers.get("location", "")
                    try:
                        url = self._check(urljoin(url, location))
                    except ValueError as exc:
                        return self._refuse("blocked", f"The page redirects somewhere ANUM does not read: {exc}", audit, "refused_redirect")
                    continue
                if status in (401, 403, 407):
                    return self._refuse("blocked", f"{host} answered {status}: the page is behind a login or blocks readers, so it is not read.", audit, "login_required")
                if status == 429:
                    return self._refuse("failed", f"{host} asked ANUM to slow down (429); try again later.", audit, "rate_limited")
                if status >= 400:
                    return self._refuse("failed", f"{host} answered HTTP {status}.", audit, "http_error")
                if truncated:
                    return self._refuse("failed", f"The page is larger than {self.max_bytes // 1000} KB, so it was not read.", audit, "too_large")
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in TEXT_TYPES:
                    return self._refuse("failed", f"The page is {content_type}, not a web page.", audit, "not_text")
                encoding = headers.get("content-encoding", "identity").strip().lower()
                if encoding not in ("", "identity"):
                    return self._refuse("failed", "The site sent a compressed answer ANUM did not ask for.", audit, "compressed")
                html = body.decode("utf-8", errors="replace")
                if CHALLENGE.search(html[:200_000]):
                    return self._refuse("blocked", f"The page on {host} is behind a login or a captcha, so it is not read.", audit, "challenge")
                from .signals import page_title, visible_text

                audit["outcome"] = "succeeded"
                return ToolResult(
                    status="succeeded",
                    summary=f"Read {url} ({len(body)} bytes).",
                    output={
                        "url": raw, "final_url": url, "status_code": status, "content_type": content_type or "text/html",
                        "title": page_title(html), "html": html[:MAX_HTML_CHARS], "text": visible_text(html, MAX_TEXT_CHARS),
                    },
                    audit_metadata=audit,
                )
            return self._refuse("failed", "The page redirects too many times.", audit, "too_many_redirects")
        except UnsafeModelEndpointError:
            return self._refuse("blocked", "That address is not a public internet site, so it is not read.", audit, "refused_address")
        except (TimeoutError, httpx.TimeoutException):
            return self._refuse("failed", f"The site did not answer within {self.timeout_seconds:g} seconds.", audit, "timeout")
        except httpx.HTTPError as exc:
            return self._refuse("failed", f"The site could not be reached ({type(exc).__name__}).", audit, "unreachable")


def fetch_definition() -> ToolDefinition:
    return ToolDefinition(
        name=FETCH_TOOL,
        description="Read one public https web page (obeys robots.txt; no logins, captchas or query strings). Arguments: url.",
        risk_level=RiskLevel.LOW,
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=30,
        idempotent=True,
        category="general",
        action_kind="read",
        input_schema={"type": "object", "properties": {"url": {"type": "string", "description": "An https URL without a query string."}}, "required": ["url"]},
    )


# The process-wide fetcher (robots cache and per-host pacing are shared by every caller).
page_fetcher = PageFetcher()
