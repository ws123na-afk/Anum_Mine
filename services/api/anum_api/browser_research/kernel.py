"""Read one public page in a throwaway Chromium (docs/browser-research.md#how-a-page-is-read).

What one call does:

1. Checks the address (``guard.check_page_url``) and robots.txt.
2. Starts a fresh headless Chromium with a fresh profile: no cookies, storage, downloads,
   service workers, permissions, extensions or saved state, and nothing kept afterwards.
   Chromium is launched with every host name mapped to "not found", a proxy that does not
   exist and QUIC, DNS prefetch, pings and background networking off, so it cannot reach
   anything by itself; web sockets are closed.
3. Answers every request through :class:`Gate`: ``GET`` only; documents, scripts, style
   sheets and ``fetch``/XHR only (images, media, fonts, frames, beacons, forms that post and
   everything else are refused); each address checked and fetched through the pinned
   fetcher (resolve once, refuse non-public addresses, dial the checked address); a
   redirect of the page itself is checked and then visited as a new navigation, a redirect
   of a script or a fetch is followed by the gate, each hop checked; no ``Set-Cookie`` is
   passed to the browser; request, size and time limits.
4. Refuses pages behind a login or a captcha and anything that is not a web page (a file
   download, a PDF, an image): nothing of them is returned.
5. Returns the title, the visible text and the page's https links, cut to limits.

Page text is untrusted data: callers keep it labelled (``prompt_provenance``).
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .guard import REDIRECT_CODES, USER_AGENT, PinnedFetcher, Refused, RobotsRules, check_page_url, check_request_url

DOCUMENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
ALLOWED_RESOURCE_TYPES = frozenset({"document", "script", "stylesheet", "xhr", "fetch"})
# The only response headers handed to Chromium: never Set-Cookie, never a redirect.
PASSED_HEADERS = ("content-type", "content-language")
ACCEPT = {
    "document": "text/html,application/xhtml+xml,text/plain;q=0.9",
    "script": "*/*",
    "stylesheet": "text/css,*/*;q=0.1",
    "xhr": "*/*",
    "fetch": "*/*",
}
CHALLENGE = re.compile(
    r"g-recaptcha|hcaptcha|cf-challenge|challenge-platform|cf_chl_|just a moment\.\.\.|attention required! \| cloudflare",
    re.IGNORECASE,
)
# Chromium switches: no network of its own (every request is answered by the gate).
CHROMIUM_ARGS = (
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--disable-quic",
    "--dns-prefetch-disable",
    "--no-pings",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-sync",
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-features=NetworkPrediction,MediaRouter,OptimizationHints,AutofillServerCommunication,InterestFeedContentSuggestions",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--no-first-run",
    "--mute-audio",
    "--disable-gpu",
    "--disable-dev-shm-usage",
)
# A proxy nobody listens on: anything that escaped interception fails to connect.
DEAD_PROXY = {"server": "http://127.0.0.1:9", "bypass": "<-loopback>"}
LINKS_SCRIPT = """() => Array.from(document.querySelectorAll('a[href]')).slice(0, 2000)
  .map(a => [a.href, (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 200)])"""
TEXT_SCRIPT = "() => (document.body ? document.body.innerText : '')"


# The answer to a page navigation that is refused while a page is shown: Chromium keeps the
# page it has (an aborted navigation would replace it with an error page).
STAY: tuple[int, dict[str, str], bytes] = (204, {}, b"")


@dataclass(frozen=True)
class Limits:
    deadline_seconds: float = 30.0
    request_timeout_seconds: float = 10.0
    max_requests: int = 60
    max_document_bytes: int = 1_500_000
    max_resource_bytes: int = 1_500_000
    max_total_bytes: int = 6_000_000
    max_redirects: int = 3
    max_text_chars: int = 20_000
    max_links: int = 40


@dataclass
class Stats:
    requests: int = 0
    blocked: int = 0
    bytes: int = 0
    reasons: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {"requests": self.requests, "blocked": self.blocked, "bytes": self.bytes, "blocked_reasons": dict(self.reasons)}


class Gate:
    """Answers every request Chromium makes. Independent of Playwright, so tests can drive it."""

    def __init__(self, fetcher: PinnedFetcher, robots: RobotsRules, limits: Limits) -> None:
        self.fetcher = fetcher
        self.robots = robots
        self.limits = limits
        self.stats = Stats()
        self.expected: str | None = None  # the page navigation the kernel is making now
        self.locked = False  # after the page loaded, no further page navigations
        self.redirect_to: str | None = None
        self.refusal: Refused | None = None
        self.document_status: int | None = None
        self.document_type: str = ""

    def begin(self, url: str) -> None:
        self.expected = url
        self.redirect_to = None
        self.refusal = None
        self.document_status = None

    def _block(self, code: str) -> None:
        self.stats.blocked += 1
        self.stats.reasons[code] += 1

    async def answer(self, *, url: str, method: str, resource_type: str, main_frame: bool) -> tuple[int, dict[str, str], bytes] | None:
        """The response to hand to Chromium, or None when the request is refused."""
        if method.upper() != "GET":
            self._block("method")  # form posts, beacons and every other write
            return STAY if resource_type == "document" and main_frame else None
        if resource_type not in ALLOWED_RESOURCE_TYPES:
            self._block(f"type:{resource_type}"[:40])
            return None
        if resource_type == "document" and not main_frame:
            self._block("frame")
            return None
        if self.stats.requests >= self.limits.max_requests:
            self._block("too_many_requests")
            return None
        self.stats.requests += 1
        if resource_type == "document":
            return await self._document(url)
        try:
            target = check_request_url(url)
            for _ in range(self.limits.max_redirects + 1):
                fetched = await self.fetcher.get(target, accept=ACCEPT[resource_type], max_bytes=self._budget(self.limits.max_resource_bytes))
                self.stats.bytes += len(fetched.body)
                if fetched.status not in REDIRECT_CODES:
                    return fetched.status, _passed(fetched.headers), fetched.body
                target = check_request_url(urljoin(target, fetched.headers.get("location", "")))
            raise Refused("too_many_redirects", "Too many redirects.")
        except Refused as exc:
            self._block(exc.code)
            return None

    def _budget(self, per_response: int) -> int:
        left = self.limits.max_total_bytes - self.stats.bytes
        if left <= 0:
            raise Refused("too_large", "The page is larger than the size limit.")
        return min(per_response, left)

    async def _document(self, url: str) -> tuple[int, dict[str, str], bytes] | None:
        if self.locked or self.expected is None or _same(url) != _same(self.expected):
            # Only the navigation the kernel makes: no script, meta refresh, form or link moves
            # the page. "204 No Content" keeps the current page in place.
            self._block("navigation")
            return STAY
        try:
            await self.robots.check(url)
            fetched = await self.fetcher.get(url, accept=ACCEPT["document"], max_bytes=self._budget(self.limits.max_document_bytes))
            self.stats.bytes += len(fetched.body)
            if fetched.status in REDIRECT_CODES:
                location = fetched.headers.get("location", "")
                if not location:
                    raise Refused("http_error", "The site answered a redirect without a destination.")
                # Checked here, then visited as a new navigation (Chromium would not route it).
                self.redirect_to = check_request_url(urljoin(url, location))
                return 200, {"content-type": "text/html; charset=utf-8"}, b""
            self.document_status = fetched.status
            content_type = fetched.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            disposition = fetched.headers.get("content-disposition", "").lower()
            if fetched.status < 400 and (disposition.startswith("attachment") or (content_type and content_type not in DOCUMENT_TYPES)):
                raise Refused("not_text", f"The address is {content_type or 'a file download'}, not a web page, so it is not read.")
            self.document_type = content_type or "text/html"
            return fetched.status, _passed(fetched.headers), fetched.body
        except Refused as exc:
            self.refusal = exc
            self._block(exc.code)
            return None


def _same(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _passed(headers: dict[str, str]) -> dict[str, str]:
    return {name: headers[name] for name in PASSED_HEADERS if name in headers}


def _clean_text(text: str, limit: int) -> tuple[str, bool]:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    joined = "\n".join(line for line in lines if line)
    return joined[:limit], len(joined) > limit


def _clean_links(raw: Any, base: str, limit: int) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if len(links) >= limit:
            break
        if not isinstance(item, list) or len(item) != 2:
            continue
        href, label = str(item[0]), " ".join(str(item[1]).split())[:200]
        try:
            parts = urlsplit(href)
        except ValueError:
            continue
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or len(href) > 2048:
            continue
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        if clean in seen or clean == base:
            continue
        seen.add(clean)
        links.append({"url": clean, "text": label})
    return links


def chromium_available(executable_path: str | None = None) -> tuple[bool, str]:
    """Whether this process can start Chromium, and why not in words an operator can act on."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return False, "Not available on this server: the Playwright package is not installed in this image."
    if executable_path:
        if not os.access(executable_path, os.X_OK):
            return False, "Not available on this server: ANUM_BROWSER_CHROMIUM_PATH does not name a program."
        return True, "Chromium is installed."
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")
    # Headless launches use Chromium's headless shell (``playwright install --only-shell chromium``).
    if not os.path.isdir(browsers) or not any(name.startswith("chromium_headless_shell-") for name in os.listdir(browsers)):
        return False, "Not available on this server: Chromium is not installed in this image."
    return True, "Chromium is installed."


def _result(outcome: str, message: str, url: str, final_url: str, stats: Stats, **extra: Any) -> dict[str, Any]:
    return {"outcome": outcome, "message": message, "url": url, "final_url": final_url, "stats": stats.as_dict(), **extra}


async def browse(
    url: str,
    *,
    fetcher: PinnedFetcher | None = None,
    limits: Limits = Limits(),
    executable_path: str | None = None,
    sandbox: bool = False,
) -> dict[str, Any]:
    """Read one page. Never raises for a page problem: the outcome says what happened."""
    stats = Stats()
    try:
        start = check_page_url(url)
    except Refused as exc:
        return _result(exc.code, str(exc), url[:2048], "", stats)
    fetcher = fetcher or PinnedFetcher(timeout_seconds=limits.request_timeout_seconds)
    gate = Gate(fetcher, RobotsRules(fetcher), limits)
    try:
        async with asyncio.timeout(limits.deadline_seconds):
            return await _browse(start, gate, limits, executable_path, sandbox)
    except TimeoutError:
        return _result("timeout", f"The page did not finish within {limits.deadline_seconds:g} seconds.", start, gate.expected or start, gate.stats)


async def _browse(start: str, gate: Gate, limits: Limits, executable_path: str | None, sandbox: bool) -> dict[str, Any]:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    with tempfile.TemporaryDirectory(prefix="anum-browser-") as home:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=executable_path or None,
                chromium_sandbox=sandbox,
                proxy=DEAD_PROXY,  # type: ignore[arg-type]
                args=list(CHROMIUM_ARGS),
                # No ANUM environment (and no proxy variables) reaches Chromium.
                env={"HOME": home, "TMPDIR": home, "LANG": "en_US.UTF-8", "TZ": "UTC"},
                downloads_path=home,
            )
            try:
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    java_script_enabled=True,
                    permissions=[],
                    user_agent=USER_AGENT,
                    locale="en-US",
                    viewport={"width": 1280, "height": 900},
                )

                async def on_route(route: Any) -> None:
                    request = route.request
                    try:
                        main_frame = request.frame.parent_frame is None
                    except Exception:
                        main_frame = False
                    try:
                        answer = await gate.answer(url=request.url, method=request.method, resource_type=request.resource_type, main_frame=main_frame)
                    except Exception:
                        gate._block("error")
                        answer = None
                    try:
                        if answer is None:
                            await route.abort("blockedbyclient")
                        else:
                            status, headers, body = answer
                            await route.fulfill(status=status, headers=headers, body=body)
                    except PlaywrightError:
                        pass  # the page went away meanwhile

                await context.route("**/*", on_route)
                route_ws = getattr(context, "route_web_socket", None)
                if route_ws is not None:
                    async def close_socket(ws: Any) -> None:
                        gate._block("websocket")
                        await ws.close()

                    await route_ws("**/*", close_socket)
                page = await context.new_page()
                page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
                context.on("page", lambda extra: asyncio.ensure_future(extra.close()) if extra != page else None)

                target = start
                navigation_ms = int(max(1.0, limits.deadline_seconds - 5) * 1000)
                for _ in range(limits.max_redirects + 1):
                    gate.begin(target)
                    try:
                        # "commit": a script that tries to navigate during load cannot hold goto()
                        # until its timeout; the load state is awaited on the page itself next.
                        await page.goto(target, wait_until="commit", timeout=navigation_ms)
                    except PlaywrightError:
                        # A script that tries to navigate while the page loads interrupts goto();
                        # the page itself was answered, so it is read as it stands.
                        if gate.refusal is None and gate.redirect_to is None and gate.document_status is None:
                            return _result("unreachable", "The page could not be loaded.", start, target, gate.stats)
                    if gate.refusal is not None:
                        return _result(gate.refusal.code, str(gate.refusal), start, target, gate.stats)
                    if gate.redirect_to is None:
                        break
                    target = gate.redirect_to
                else:
                    return _result("too_many_redirects", "The page redirects too many times.", start, target, gate.stats)

                status = gate.document_status or 0
                if status in (401, 403, 407):
                    return _result("login_required", f"The site answered {status}: the page is behind a login or blocks readers, so it is not read.", start, target, gate.stats, status_code=status)
                if status == 429:
                    return _result("rate_limited", "The site asked ANUM to slow down (429); try again later.", start, target, gate.stats, status_code=status)
                if status >= 400:
                    return _result("http_error", f"The site answered HTTP {status}.", start, target, gate.stats, status_code=status)
                try:
                    # Read from the document itself: Playwright's load-state wait also waits for a
                    # navigation the gate refused (204), which never completes.
                    await page.wait_for_function("document.readyState !== 'loading'", timeout=navigation_ms, polling=100)
                except PlaywrightError:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except PlaywrightError:
                    pass  # pages that keep polling: read what is there
                gate.locked = True
                html = await page.content()
                if CHALLENGE.search(html[:200_000]) or await page.locator("input[type=password]").count():
                    return _result("challenge", "The page is behind a login or a captcha, so it is not read.", start, target, gate.stats, status_code=status)
                title = " ".join((await page.title()).split())[:300]
                text, truncated = _clean_text(str(await page.evaluate(TEXT_SCRIPT)), limits.max_text_chars)
                links = _clean_links(await page.evaluate(LINKS_SCRIPT), target, limits.max_links)
                return _result(
                    "succeeded", f"Read {target}.", start, target, gate.stats,
                    status_code=status, content_type=gate.document_type, title=title, text=text, truncated=truncated, links=links,
                )
            finally:
                await browser.close()
