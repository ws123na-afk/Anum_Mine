"""The network guard of browser research (docs/browser-research.md#network-guard).

Chromium never resolves a name or opens a connection itself: it is launched with every host
mapped to "not found" and a proxy that does not exist, and every request it makes is handed
to :class:`Gate`, which answers it from :class:`PinnedFetcher`. The fetcher:

* checks the URL (``https`` only, default port, no credentials, IP literals in standard form)
  with ``model_egress.check_endpoint`` (always enforced, in every environment);
* resolves the host **once**, refuses the request if *any* answer is loopback, private,
  shared, unique-local (``fc00::/7``), link-local, a cloud metadata address
  (``169.254.169.254``, ``fd00:ec2::254``, ``192.0.0.192``, ``100.100.100.200``), multicast,
  unspecified, reserved or documentation space, IPv4 embedded in IPv6 included;
* connects to that checked address, keeping the original ``Host`` header and TLS server
  name (``model_egress.PinnedEgressTransport``), so a second DNS answer (DNS rebinding) is
  never used;
* never follows a redirect itself: a redirect is returned to the caller, which checks the
  next address the same way before anything is fetched from it.

robots.txt is read the same way and obeyed for ``ANUM-Research`` and ``*`` (RFC 9309: a 4xx
answer means no rules; a 5xx answer or no answer means nothing on that host is read).

This module imports nothing from the API's settings, so the browser container needs no ANUM
configuration and no secrets.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..model_egress import EgressPolicy, PinnedEgressTransport, UnsafeModelEndpointError, check_endpoint

USER_AGENT = "ANUM-Research/1.0 (reads public pages for an agent; obeys robots.txt)"
ROBOTS_AGENT = "ANUM-Research"
MAX_URL_CHARS = 2048
MAX_SUBRESOURCE_URL_CHARS = 4096
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
ROBOTS_TTL_SECONDS = 3600.0
ROBOTS_MAX_BYTES = 500_000

# The guard is on everywhere, local and test included: research reads the public internet only.
POLICY = EgressPolicy(enforce=True)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class Refused(ValueError):
    """A request ANUM does not make. ``code`` is for audit; the message is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


NOT_PUBLIC = "That address is not a public internet site, so it is not read."


def check_page_url(url: str) -> str:
    """The page address an agent asked for, normalised, or :class:`Refused`.

    https only (no ``http``, ``file``, ``data``, ``javascript``, ``chrome`` or any other
    scheme), the default port, no user name or password, no query string (a research page
    never needs one, and it is the easy channel for leaking data in an address). The
    fragment is dropped.
    """
    text = (url or "").strip()
    if not text or len(text) > MAX_URL_CHARS or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise Refused("refused_url", "The page address is empty, too long or has spaces.")
    try:
        parts = urlsplit(text)
    except ValueError:
        raise Refused("refused_url", "The page address is not a valid web address.") from None
    if parts.scheme.lower() != "https":
        raise Refused("refused_scheme", "Only https pages are read.")
    if parts.username is not None or parts.password is not None:
        raise Refused("refused_url", "Page addresses with a user name or password are not read.")
    if parts.query:
        raise Refused("refused_url", "Page addresses with a query string are not read.")
    _check_endpoint(text)
    return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", "", ""))


def check_request_url(url: str) -> str:
    """A request the page itself makes (a script, a style sheet, a fetch): https, public."""
    if len(url) > MAX_SUBRESOURCE_URL_CHARS or any(ord(ch) < 32 for ch in url):
        raise Refused("refused_url", "The request address is too long.")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise Refused("refused_url", "The request address is not valid.") from None
    if parts.scheme.lower() != "https":
        raise Refused("refused_scheme", "Only https requests are made.")
    if parts.username is not None or parts.password is not None:
        raise Refused("refused_url", "Requests with a user name or password are not made.")
    _check_endpoint(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))


def _check_endpoint(url: str) -> None:
    try:
        check_endpoint(url, POLICY)
    except UnsafeModelEndpointError:
        raise Refused("refused_address", NOT_PUBLIC) from None


@dataclass(frozen=True)
class Fetched:
    status: int
    headers: dict[str, str]
    body: bytes
    # The checked address the request was sent to (tests prove it is the one resolved).
    url: str


class PinnedFetcher:
    """One GET at a time through the pinned transport. Tests pass ``inner`` and ``resolve``."""

    def __init__(
        self,
        *,
        resolve: Resolver | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._resolve = resolve
        self._inner = inner
        self.timeout_seconds = timeout_seconds

    async def get(self, url: str, *, accept: str, max_bytes: int) -> Fetched:
        """GET ``url`` (already checked) or raise :class:`Refused`. Redirects come back as 3xx."""
        transport = PinnedEgressTransport(POLICY, resolve=self._resolve, inner=self._inner)
        headers = {"user-agent": USER_AGENT, "accept": accept, "accept-encoding": "identity", "accept-language": "en,ar;q=0.8"}
        try:
            async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False, timeout=self.timeout_seconds) as client:
                async with asyncio.timeout(self.timeout_seconds):
                    async with client.stream("GET", url, headers=headers) as response:
                        encoding = response.headers.get("content-encoding", "identity").strip().lower()
                        if encoding not in ("", "identity"):
                            raise Refused("compressed", "The site sent a compressed answer ANUM did not ask for.")
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise Refused("too_large", f"The answer is larger than {max_bytes // 1000} KB, so it was not read.")
                            chunks.append(chunk)
                        return Fetched(
                            status=response.status_code,
                            headers={key.lower(): value for key, value in response.headers.items()},
                            body=b"".join(chunks),
                            url=url,
                        )
        except UnsafeModelEndpointError:
            raise Refused("refused_address", NOT_PUBLIC) from None
        except (TimeoutError, httpx.TimeoutException):
            raise Refused("timeout", f"The site did not answer within {self.timeout_seconds:g} seconds.") from None
        except httpx.HTTPError as exc:
            raise Refused("unreachable", f"The site could not be reached ({type(exc).__name__}).") from None


@dataclass
class _Robots:
    parser: RobotFileParser | None  # None: nothing on the host may be read
    fetched_at: float


class RobotsRules:
    """robots.txt per host, read through the same pinned fetcher, cached for an hour."""

    def __init__(self, fetcher: PinnedFetcher, clock: Callable[[], float] = time.monotonic) -> None:
        self._fetcher = fetcher
        self._clock = clock
        self._cache: dict[str, _Robots] = {}

    async def check(self, url: str) -> None:
        """Raise :class:`Refused` unless robots.txt lets ``ANUM-Research`` read ``url``."""
        parts = urlsplit(url)
        host = parts.netloc.lower()
        entry = self._cache.get(host)
        if entry is None or self._clock() - entry.fetched_at >= ROBOTS_TTL_SECONDS:
            entry = await self._read(host)
            self._cache[host] = entry
        if entry.parser is None:
            raise Refused("robots_unavailable", f"{parts.hostname} did not answer for robots.txt, so none of its pages are read.")
        if not entry.parser.can_fetch(ROBOTS_AGENT, url):
            raise Refused("robots_disallowed", f"robots.txt on {parts.hostname} does not allow reading {parts.path or '/'}.")

    async def _read(self, host: str) -> _Robots:
        url = f"https://{host}/robots.txt"
        for _ in range(3):  # the file and at most two redirects, each checked like any request
            try:
                fetched = await self._fetcher.get(url, accept="text/plain", max_bytes=ROBOTS_MAX_BYTES)
            except Refused as exc:
                if exc.code == "refused_address":
                    raise
                return _Robots(parser=None, fetched_at=self._clock())
            if fetched.status in REDIRECT_CODES:
                try:
                    url = check_request_url(urljoin(url, fetched.headers.get("location", "")))
                except Refused:
                    return _Robots(parser=None, fetched_at=self._clock())
                continue
            if 200 <= fetched.status < 300:
                parser = RobotFileParser()
                parser.parse(fetched.body.decode("utf-8", errors="replace").splitlines())
                return _Robots(parser=parser, fetched_at=self._clock())
            if 400 <= fetched.status < 500:
                # RFC 9309: unavailable (4xx) means no rules.
                parser = RobotFileParser()
                parser.parse([])
                return _Robots(parser=parser, fetched_at=self._clock())
            break
        return _Robots(parser=None, fetched_at=self._clock())
