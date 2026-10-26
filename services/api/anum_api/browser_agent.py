"""Guarded, read-only Playwright browser execution for ANUM agent workers.

This module deliberately does not run inside the API process by default. Browser
dependencies are optional and a dedicated sandbox worker must supply them.
Every request is scoped to explicit public hosts; forms, JavaScript evaluation,
uploads, downloads and state-changing clicks are intentionally unsupported.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MAX_ACTIONS = 20
MAX_TEXT = 16000
MAX_SELECTOR = 300
MAX_HOSTS = 15


class BrowserRejected(ValueError):
    """A request crosses the browser worker security boundary."""


def validate_host(host: str) -> str:
    host = host.lower().rstrip(".")
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise BrowserRejected("Invalid public hostname")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
        raise BrowserRejected("Internal host is not permitted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise BrowserRejected("IP-literal destinations are not permitted")


async def verify_public_dns(host: str) -> None:
    """Fail closed on private, loopback, reserved or unresolvable addresses."""
    try:
        records = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise BrowserRejected("Destination DNS cannot be verified") from exc
    if not records:
        raise BrowserRejected("Destination DNS is empty")
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0])
        except ValueError as exc:
            raise BrowserRejected("Invalid destination address") from exc
        if not address.is_global:
            raise BrowserRejected("Destination points to a non-public address")


def checked_url(url: str, allowed_hosts: frozenset[str]) -> str:
    if len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise BrowserRejected("Invalid navigation URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise BrowserRejected("Only public HTTPS URLs without credentials are allowed")
    host = validate_host(parsed.hostname)
    if host not in allowed_hosts or parsed.port not in (None, 443):
        raise BrowserRejected("Destination not in this task's permitted hosts")
    return url


@dataclass(frozen=True)
class BrowserAction:
    kind: str
    value: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"navigate", "read", "click_link", "screenshot"}:
            raise BrowserRejected("Unsupported action: use read-only browser actions")
        if len(self.value) > 2048:
            raise BrowserRejected("Action value is too long")
        if self.kind == "click_link" and (not self.value or len(self.value) > MAX_SELECTOR):
            raise BrowserRejected("Link selector is missing or too long")


@dataclass(frozen=True)
class BrowserRequest:
    allowed_hosts: frozenset[str]
    actions: tuple[BrowserAction, ...]
    timeout_seconds: int = 45

    def __post_init__(self) -> None:
        if not self.allowed_hosts or len(self.allowed_hosts) > MAX_HOSTS:
            raise BrowserRejected("Supply 1 to 15 permitted public hosts")
        for host in self.allowed_hosts:
            if validate_host(host) != host:
                raise BrowserRejected("Host must be normalized")
        if not self.actions or len(self.actions) > MAX_ACTIONS:
            raise BrowserRejected("Supply 1 to 20 actions")
        if not (5 <= self.timeout_seconds <= 120):
            raise BrowserRejected("Browser time limit must be 5 to 120 seconds")
        for action in self.actions:
            if action.kind == "navigate":
                checked_url(action.value, self.allowed_hosts)


@dataclass
class BrowserStep:
    index: int
    kind: str
    url: str
    status: str
    text: str = ""
    screenshot: bytes | None = field(default=None, repr=False)
    error: str = ""


async def execute_browser(request: BrowserRequest) -> list[BrowserStep]:
    """Execute in a short-lived Chromium context without persistent credentials.

    The caller must isolate the worker's network namespace and filesystem as
    browser request interception is defense-in-depth, not a sandbox boundary.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserRejected("Browser worker is not installed") from exc

    for host in request.allowed_hosts:
        await verify_public_dns(host)

    async def run() -> list[BrowserStep]:
        results: list[BrowserStep] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-first-run"])
            try:
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    permissions=[],
                    java_script_enabled=True,
                )
                page = await context.new_page()

                async def intercept(route):
                    url = route.request.url
                    try:
                        checked_url(url, request.allowed_hosts)
                        await verify_public_dns(validate_host(urlsplit(url).hostname or ""))
                        if route.request.method != "GET":
                            await route.abort()
                        else:
                            await route.continue_()
                    except (BrowserRejected, ValueError):
                        await route.abort()

                await context.route("**/*", intercept)
                for index, action in enumerate(request.actions):
                    try:
                        if action.kind == "navigate":
                            await page.goto(action.value, wait_until="domcontentloaded", timeout=15000)
                        elif action.kind == "click_link":
                            link = page.locator(action.value)
                            if await link.count() != 1:
                                raise BrowserRejected("Link selector must match exactly one element")
                            if await link.evaluate("(el) => el.tagName.toLowerCase() !== 'a'"):
                                raise BrowserRejected("Only navigation links can be clicked")
                            href = await link.get_attribute("href")
                            if not href:
                                raise BrowserRejected("Link has no destination")
                            from urllib.parse import urljoin
                            destination = urljoin(page.url, href)
                            checked_url(destination, request.allowed_hosts)
                            await verify_public_dns(validate_host(urlsplit(destination).hostname or ""))
                            await page.goto(destination, wait_until="domcontentloaded", timeout=15000)
                        elif action.kind == "read":
                            text = (await page.locator("body").inner_text(timeout=8000))[:MAX_TEXT]
                            results.append(BrowserStep(index, action.kind, page.url, "completed", text=text))
                            continue
                        elif action.kind == "screenshot":
                            screenshot = await page.screenshot(full_page=False, animations="disabled")
                            results.append(BrowserStep(index, action.kind, page.url, "completed", screenshot=screenshot))
                            continue
                        results.append(BrowserStep(index, action.kind, page.url, "completed"))
                    except Exception as exc:
                        results.append(BrowserStep(index, action.kind, page.url, "failed", error=type(exc).__name__))
                        break
                await context.close()
            finally:
                await browser.close()
        return results

    return await asyncio.wait_for(run(), timeout=request.timeout_seconds)
