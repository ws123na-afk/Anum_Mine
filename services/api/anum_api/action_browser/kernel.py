"""One action-browser session in Chromium (docs/action-browser.md#the-browser-service-in-action-mode).

Runs inside the browser service only. A session is one Chromium with a fresh in-memory
context for the life of one task: cookies are kept while the task runs and nothing is kept
after :meth:`ActionSession.close`. Chromium's only route out is the session's
:class:`~.proxy.EgressProxy`; top-level navigations are also held to the approved sites.

What the API sees of a page is a *snapshot*: the address, title, visible text and the
interactive elements, each with a reference. Password, card and one-time-code fields, and
every field a saved secret was typed into, are masked: a secret typed here never leaves.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import tempfile
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..browser_research.kernel import CHROMIUM_ARGS
from .classify import detect_amounts, is_card_field, is_otp_field
from .handles import host_in_sites, parse_handle
from .proxy import Connector, EgressProxy, Resolver

VIEWPORT = {"width": 1280, "height": 800}
MAX_ELEMENTS = 150
MAX_FRAME_ELEMENTS = 40
MAX_TEXT_CHARS = 6000
MAX_INSPECT_TEXT = 8000
# Files a task may download into the workspace's Files (docs/action-browser.md#downloads). Never opened here.
DOWNLOAD_EXTENSIONS = frozenset({".pdf", ".csv", ".xlsx", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".json"})
MAX_DOWNLOAD_BYTES = 20_000_000
MAX_DOWNLOADS = 10
OWNER_KEYS = frozenset({"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Space"})
PAYMENT_FRAME_HOSTS = (
    "stripe.com", "stripe.network", "adyen.com", "checkout.com", "paypal.com", "braintreegateway.com", "squareup.com", "paytabs.com", "telr.com",
    "network.ae", "ngenius-payments.com", "oppwa.com", "hyperpay.com", "tap.company", "gotapnow.com", "moyasar.com", "payfort.com",
    "amazonpaymentservices.com", "cardinalcommerce.com",
)

# Elements an agent may act on. Each gets the session's reference attribute.
SNAPSHOT_JS = r"""
([attr, secretAttr, limit, prefix]) => {
  const cardRe = /card.?(number|num|no\b)|credit.?card|\bcc.?(num|number|exp|csc|cvv)|cvv|cvc|security.?code|expir|\biban\b|رقم\s*البطاقة|البطاقة|رمز\s*الأمان/i;
  const otpRe = /\botp\b|one.?time|verification.?code|2fa|two.?factor|auth(entication)?.?code|sms.?code|رمز\s*التحقق/i;
  const sel = 'a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=option],[role=menuitem],[contenteditable="true"],summary';
  const visible = (el) => { const r = el.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const clean = (v, n) => String(v || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const labelOf = (el) => clean(el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || '', 120);
  let next = Number(document.documentElement.getAttribute(attr + '-n') || '0');
  const out = [];
  const forms = Array.from(document.forms);
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= limit) break;
    if (!visible(el)) continue;
    let ref = el.getAttribute(attr);
    if (!ref || !ref.startsWith(prefix)) { next += 1; ref = prefix + next; el.setAttribute(attr, ref); }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const name = clean(el.getAttribute('name') || '', 80), id = clean(el.id || '', 80);
    const autocomplete = clean(el.getAttribute('autocomplete') || '', 60).toLowerCase();
    const label = labelOf(el);
    const probe = name + ' ' + id + ' ' + label + ' ' + autocomplete;
    const secret = el.getAttribute(secretAttr);
    const sensitive = type === 'password' || autocomplete.startsWith('cc-') || cardRe.test(probe) || otpRe.test(probe) || autocomplete === 'one-time-code' || Boolean(secret);
    const item = { ref, tag, type, role: clean(el.getAttribute('role') || '', 30), name, id, autocomplete, label,
      text: clean(tag === 'input' ? '' : el.innerText, 100) };
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      const raw = tag === 'select' ? (el.selectedOptions[0] ? el.selectedOptions[0].text : '') : (type === 'checkbox' || type === 'radio' ? '' : el.value);
      item.value = sensitive ? (raw ? (secret || '(hidden)') : '') : clean(raw, 100);
      if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
      if (type === 'submit' || type === 'button') item.text = clean(el.value, 100);
    }
    if (tag === 'select') item.options = Array.from(el.options).slice(0, 15).map((o) => clean(o.text, 60));
    if (tag === 'a') item.href = clean(el.href, 300);
    if (el.disabled) item.disabled = true;
    if (el.form) item.form = forms.indexOf(el.form);
    out.push(item);
  }
  document.documentElement.setAttribute(attr + '-n', String(next));
  return out;
}
"""

DESCRIBE_JS = r"""
(el, secretAttr) => {
  const clean = (v, n) => String(v || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const labelOf = (x) => clean(x.getAttribute('aria-label') || (x.labels && x.labels[0] && x.labels[0].innerText) || x.getAttribute('placeholder') || x.getAttribute('title') || x.getAttribute('alt') || '', 120);
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const form = el.form || el.closest('form');
  const out = { tag, type, role: clean(el.getAttribute('role') || '', 30), name: clean(el.getAttribute('name') || '', 80), id: clean(el.id || '', 80),
    autocomplete: clean(el.getAttribute('autocomplete') || '', 60).toLowerCase(), label: labelOf(el),
    text: clean(tag === 'input' ? el.value : el.innerText, 200), title: clean(el.getAttribute('title') || '', 120), aria: clean(el.getAttribute('aria-label') || '', 120),
    href: tag === 'a' ? clean(el.href, 500) : '', download: tag === 'a' && el.hasAttribute('download'), disabled: Boolean(el.disabled),
    secret: Boolean(el.getAttribute(secretAttr)) };
  if (type === 'password' || out.autocomplete.startsWith('cc-') || out.secret) out.text = '';
  out.submits = (tag === 'button' && (type === '' || type === 'submit') && Boolean(form)) || (tag === 'input' && (type === 'submit' || type === 'image'));
  if (form) {
    const fields = Array.from(form.querySelectorAll('input:not([type=hidden]),select,textarea')).slice(0, 40).map((f) => ({
      name: clean(f.getAttribute('name') || '', 80), id: clean(f.id || '', 80), type: (f.getAttribute('type') || f.tagName).toLowerCase(),
      autocomplete: clean(f.getAttribute('autocomplete') || '', 60).toLowerCase(), label: labelOf(f), placeholder: clean(f.getAttribute('placeholder') || '', 80) }));
    out.form = { action: clean(form.action || '', 500), method: clean(form.method || 'get', 10).toLowerCase(), fields, text: clean(form.innerText, 1200),
      key: Array.from(document.forms).indexOf(form) + ':' + fields.map((f) => f.name + '/' + f.type).join(',') };
  }
  return out;
}
"""

PAGE_JS = r"""
([limit]) => {
  const clean = (v, n) => String(v || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const labelOf = (x) => clean(x.getAttribute('aria-label') || (x.labels && x.labels[0] && x.labels[0].innerText) || x.getAttribute('placeholder') || '', 120);
  const fields = Array.from(document.querySelectorAll('input:not([type=hidden]),select,textarea')).slice(0, 60).map((f) => {
    const r = f.getBoundingClientRect();
    return { name: clean(f.getAttribute('name') || '', 80), id: clean(f.id || '', 80), type: (f.getAttribute('type') || f.tagName).toLowerCase(),
      autocomplete: clean(f.getAttribute('autocomplete') || '', 60).toLowerCase(), label: labelOf(f), visible: r.width > 0 && r.height > 0 };
  });
  const html = document.documentElement ? document.documentElement.outerHTML.slice(0, 200000) : '';
  const challenge = /g-recaptcha|h-captcha|cf-turnstile|challenge-platform|cf_chl_/i.test(html);
  return { title: clean(document.title, 300), text: (document.body ? document.body.innerText : '').slice(0, limit), fields, challenge };
}
"""

MARK_MASKS_JS = r"""
([maskAttr, secretAttr, payHosts]) => {
  const cardRe = /card.?(number|num|no\b)|credit.?card|\bcc.?(num|number|exp|csc|cvv)|cvv|cvc|security.?code|expir|\biban\b|رقم\s*البطاقة|البطاقة|رمز\s*الأمان/i;
  for (const el of document.querySelectorAll('[' + maskAttr + ']')) el.removeAttribute(maskAttr);
  for (const el of document.querySelectorAll('input,textarea,select,[contenteditable="true"]')) {
    const probe = [el.getAttribute('name'), el.id, el.getAttribute('autocomplete'), el.getAttribute('aria-label'), el.getAttribute('placeholder'), el.labels && el.labels[0] && el.labels[0].innerText].join(' ');
    if ((el.getAttribute('type') || '').toLowerCase() === 'password' || (el.getAttribute('autocomplete') || '').toLowerCase().startsWith('cc-') || cardRe.test(probe) || el.hasAttribute(secretAttr)) el.setAttribute(maskAttr, '1');
  }
  for (const frame of document.querySelectorAll('iframe')) {
    let host = '';
    try { host = new URL(frame.src, location.href).hostname.toLowerCase(); } catch (e) { host = ''; }
    if (frame.hasAttribute(secretAttr) || payHosts.some((h) => host === h || host.endsWith('.' + h))) frame.setAttribute(maskAttr, '1');
  }
}
"""


class ActionError(Exception):
    """A step that could not be done. ``code`` is for audit; the message is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _without_fragment(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url[:2048]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))[:2048]


def fingerprint(described: dict[str, Any]) -> str:
    """SHA-256 of what an approval is about: the address, the target and its form, the amounts and the frames.

    Typed values are left out (filling a field does not change it); amounts are in (a new price does)."""
    page = described.get("page") or {}
    target = described.get("target") or {}
    form = target.get("form") or {}
    basis = {
        "url": _without_fragment(str(page.get("url") or "")),
        "target": {key: target.get(key) for key in ("tag", "type", "role", "name", "id", "label", "aria", "href", "frame_host")},
        # A field's text is what was typed into it; a button's text (an input's value too) is its label.
        "target_text": "" if target.get("tag") in {"textarea", "select"} or (
            target.get("tag") == "input" and target.get("type") not in {"submit", "button", "image", "reset"}) else target.get("text"),
        "form": {"action": form.get("action"), "method": form.get("method"), "fields": [(f.get("name"), f.get("type"), f.get("label")) for f in form.get("fields") or []]},
        "amounts": sorted({amount.text for amount in detect_amounts(str(page.get("text") or ""))}),
        "frames": sorted(set(page.get("frames") or [])),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


@dataclass
class SessionLimits:
    idle_seconds: float = 900.0
    max_seconds: float = 1800.0
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 25_000
    max_bytes: int = 25_000_000
    max_tunnels: int = 200


class ActionSession:
    """One task's browser. Every operation holds the session lock: one step at a time."""

    def __init__(
        self,
        session_id: str,
        sites: list[str],
        *,
        limits: SessionLimits | None = None,
        resolve: Resolver | None = None,
        connect: Connector | None = None,
        ignore_https_errors: bool = False,
    ) -> None:
        self.id = session_id
        self.sites = list(dict.fromkeys(site.lower() for site in sites if site))[:20]
        self.limits = limits or SessionLimits()
        self.created = time.monotonic()
        self.last_used = self.created
        self.proxy = EgressProxy(resolve=resolve, connect=connect, max_bytes=self.limits.max_bytes, max_tunnels=self.limits.max_tunnels)
        self.lock = asyncio.Lock()
        self.held: str | None = None
        self.blocked_navigations = 0
        self._token = secrets.token_hex(4)
        self.ref_attr = f"data-ab-{self._token}"
        self.secret_attr = f"data-ab-s{self._token}"
        self.mask_attr = f"data-ab-m{self._token}"
        self._ignore_https_errors = ignore_https_errors
        self._home: tempfile.TemporaryDirectory[str] | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None
        self.closed = False
        # Finished downloads: {"id", "name", "size", "path"} (the path never leaves this process).
        self.downloads: list[dict[str, Any]] = []
        self.refused_downloads = 0
        self._reported_downloads = 0
        self._pending_downloads: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ life cycle

    async def start(self, executable_path: str | None = None, sandbox: bool = False) -> None:
        from playwright.async_api import async_playwright

        port = await self.proxy.start()
        self._home = tempfile.TemporaryDirectory(prefix="anum-act-")
        home = self._home.name
        args = [arg if not arg.startswith("--host-resolver-rules") else "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" for arg in CHROMIUM_ARGS]
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            executable_path=executable_path or None,
            chromium_sandbox=sandbox,
            proxy={"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"},
            # Tests only (a local site with a self-signed certificate; never set by the service): Chromium
            # cancels downloads from a page whose certificate error was merely ignored.
            args=args + (["--ignore-certificate-errors"] if self._ignore_https_errors else []),
            # No ANUM environment (and no proxy variables) reaches Chromium.
            env={"HOME": home, "TMPDIR": home, "LANG": "en_US.UTF-8", "TZ": "UTC"},
            downloads_path=home,
        )
        self._context = await self._browser.new_context(
            # Downloads land in this session's temporary folder only and are never opened here;
            # the API checks the type and size again and stores them in Files.
            accept_downloads=True,
            service_workers="block",
            permissions=[],
            locale="en-US",
            viewport=VIEWPORT,
            ignore_https_errors=self._ignore_https_errors,
        )
        await self._context.route("**/*", self._route)
        self.page = await self._context.new_page()
        self.page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        self.page.on("download", self._on_download)
        # File uploads are not part of this change: a file chooser is never answered.
        self._context.on("page", lambda extra: asyncio.ensure_future(extra.close()) if extra != self.page else None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(Exception):
            if self._browser is not None:
                await self._browser.close()
        with contextlib.suppress(Exception):
            if self._playwright is not None:
                await self._playwright.stop()
        await self.proxy.close()
        if self._home is not None:
            with contextlib.suppress(Exception):
                self._home.cleanup()

    def _on_download(self, download: Any) -> None:
        task = asyncio.ensure_future(self._keep_download(download))
        self._pending_downloads.add(task)
        task.add_done_callback(self._pending_downloads.discard)

    async def _keep_download(self, download: Any) -> None:
        import os
        from pathlib import PurePosixPath

        name = "".join(ch for ch in str(download.suggested_filename or "download")[:120] if ch.isalnum() or ch in "._- ").strip() or "download"
        extension = PurePosixPath(name.lower()).suffix
        if extension not in DOWNLOAD_EXTENSIONS or len(self.downloads) >= MAX_DOWNLOADS or self._home is None:
            self.refused_downloads += 1
            with contextlib.suppress(Exception):
                await download.cancel()
            return
        target = os.path.join(self._home.name, f"dl-{len(self.downloads) + self.refused_downloads}-{secrets.token_hex(4)}{extension}")
        try:
            await download.save_as(target)
            size = os.path.getsize(target)
        except Exception:
            self.refused_downloads += 1
            return
        if size <= 0 or size > MAX_DOWNLOAD_BYTES:
            self.refused_downloads += 1
            with contextlib.suppress(OSError):
                os.remove(target)
            return
        self.downloads.append({"id": f"d{len(self.downloads) + 1}", "name": name, "size": size, "path": target})

    async def wait_downloads(self, timeout: float = 30.0) -> None:
        if self._pending_downloads:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.gather(*list(self._pending_downloads), return_exceptions=True), timeout=timeout)

    def download_bytes(self, download_id: str) -> tuple[str, bytes] | None:
        for item in self.downloads:
            if item["id"] == download_id:
                with open(item["path"], "rb") as handle:
                    return item["name"], handle.read(MAX_DOWNLOAD_BYTES + 1)
        return None

    def expired(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return now - self.last_used > self.limits.idle_seconds or now - self.created > self.limits.max_seconds

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def add_sites(self, sites: list[str]) -> None:
        for site in sites:
            site = site.lower()
            if site and site not in self.sites and len(self.sites) < 20:
                self.sites.append(site)

    async def _route(self, route: Any) -> None:
        request = route.request
        try:
            main = request.is_navigation_request() and request.frame.parent_frame is None
        except Exception:
            main = False
        if main:
            try:
                parts = urlsplit(request.url)
                allowed = parts.scheme == "https" and host_in_sites((parts.hostname or "").lower(), self.sites)
            except ValueError:
                allowed = False
            if not allowed:
                # Held, never loaded: the owner decides whether to open a site off the list.
                self.held = _without_fragment(request.url)
                self.blocked_navigations += 1
                with contextlib.suppress(Exception):
                    await route.fulfill(status=204, body=b"")
                return
        with contextlib.suppress(Exception):
            await route.continue_()

    # ------------------------------------------------------------------ reading

    def _frames(self) -> list[Any]:
        main = self.page.main_frame
        others = [frame for frame in self.page.frames if frame is not main][:5]
        return [main, *others]

    async def snapshot(self) -> dict[str, Any]:
        self.touch()
        elements: list[dict[str, Any]] = []
        frames_info: list[str] = []
        for index, frame in enumerate(self._frames()):
            prefix = "e" if index == 0 else f"f{index}e"
            limit = MAX_ELEMENTS - len(elements) if index == 0 else min(MAX_FRAME_ELEMENTS, MAX_ELEMENTS - len(elements))
            if limit <= 0:
                break
            try:
                found = await frame.evaluate(SNAPSHOT_JS, [self.ref_attr, self.secret_attr, limit, prefix])
            except Exception:
                found = []
            if index > 0:
                frames_info.append(_host(frame.url))
            for item in found if isinstance(found, list) else []:
                if isinstance(item, dict):
                    if index > 0:
                        item["frame_host"] = _host(frame.url)
                    elements.append(_masked(item))
        page = await self._page_info(MAX_TEXT_CHARS)
        snapshot = {**page, "elements": elements, "held": self.held}
        # Downloads finished since the last snapshot (names and sizes only; the bytes stay here).
        await self.wait_downloads()
        fresh = self.downloads[self._reported_downloads:]
        if fresh:
            self._reported_downloads = len(self.downloads)
            snapshot["downloads"] = [{key: item[key] for key in ("id", "name", "size")} for item in fresh]
        return snapshot

    async def _page_info(self, text_limit: int) -> dict[str, Any]:
        url = self.page.url or ""
        try:
            info = await self.page.evaluate(PAGE_JS, [text_limit])
        except Exception:
            info = {"title": "", "text": "", "fields": [], "challenge": False}
        frame_urls = [_without_query(frame.url) for frame in self.page.frames if frame is not self.page.main_frame][:20]
        return {
            "url": _without_fragment(url), "host": _host(url), "path": urlsplit(url).path if url else "",
            "title": str(info.get("title") or ""), "text": str(info.get("text") or ""), "fields": info.get("fields") or [],
            "challenge": bool(info.get("challenge")), "frames": sorted({_host(u) for u in frame_urls if _host(u)}), "frame_urls": frame_urls,
        }

    async def _locate(self, ref: str) -> tuple[Any, Any]:
        """The frame and the one element ``ref`` names, or :class:`ActionError`."""
        if not ref or len(ref) > 12 or not all(ch.isalnum() for ch in ref):
            raise ActionError("bad_ref", "That element reference is not valid.")
        index = 0
        if ref.startswith("f") and "e" in ref:
            try:
                index = int(ref[1:ref.index("e")])
            except ValueError:
                raise ActionError("bad_ref", "That element reference is not valid.") from None
        frames = self._frames()
        if index >= len(frames):
            raise ActionError("no_target", "That element is not on the page any more.")
        frame = frames[index]
        locator = frame.locator(f'[{self.ref_attr}="{ref}"]')
        count = await locator.count()
        if count != 1:
            raise ActionError("no_target" if count == 0 else "ambiguous", "That element is not on the page any more." if count == 0 else "More than one element answers to that reference; the page was read again.")
        return frame, locator

    async def inspect(self, action: dict[str, Any]) -> dict[str, Any]:
        """What a step would act on, without acting: the target, its form, the page and the fingerprint."""
        self.touch()
        kind = str(action.get("type") or "")
        target: dict[str, Any] = {}
        if kind in {"click", "type", "select"}:
            frame, locator = await self._locate(str(action.get("ref") or ""))
            target = await locator.evaluate(DESCRIBE_JS, self.secret_attr)
            if frame is not self.page.main_frame:
                target["frame_host"] = _host(frame.url)
        page = await self._page_info(MAX_INSPECT_TEXT)
        described = {"action": _public_action(action), "page": page, "target": target}
        described["fingerprint"] = fingerprint(described)
        return described

    # ------------------------------------------------------------------ acting

    async def act(self, action: dict[str, Any], expect: dict[str, Any] | None, secret: str | None = None) -> dict[str, Any]:
        """Do one step if the page is still what was classified (``expect``: url and fingerprint)."""
        self.touch()
        kind = str(action.get("type") or "")
        if expect:
            try:
                fresh = await self.inspect(action)
            except ActionError:
                # The element the owner approved is gone (the page was replaced): nothing is done.
                return {"outcome": "page_changed"}
            if _without_fragment(str(expect.get("url") or "")) != fresh["page"]["url"] or expect.get("fingerprint") != fresh["fingerprint"]:
                return {"outcome": "page_changed", "inspect": fresh}
        self.held = None
        timeout = self.limits.action_timeout_ms
        try:
            if kind == "navigate":
                url = str(action.get("url") or "")
                with contextlib.suppress(Exception):
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=self.limits.navigation_timeout_ms)
            elif kind in {"click", "type", "select"}:
                _, locator = await self._locate(str(action.get("ref") or ""))
                if kind == "click":
                    await locator.click(timeout=timeout)
                elif kind == "select":
                    value = str(action.get("value") or "")
                    try:
                        await locator.select_option(label=value, timeout=timeout)
                    except Exception:
                        await locator.select_option(value=value, timeout=timeout)
                else:
                    raw = str(action.get("value") or "")
                    handle = parse_handle(raw)
                    if handle is not None and secret is None:
                        raise ActionError("secret_missing", "The saved value was not handed to the browser.")
                    await locator.fill(secret if handle is not None else raw, timeout=timeout)
                    if handle is not None:
                        # Masked from now on in snapshots and pictures; the handle stands in for the value.
                        await locator.evaluate("(el, [a, v]) => el.setAttribute(a, v)", [self.secret_attr, handle.text])
                    if action.get("submit"):
                        await locator.press("Enter", timeout=timeout)
            elif kind == "scroll":
                await self.page.mouse.wheel(0, 700 if str(action.get("value") or "down") != "up" else -700)
            elif kind == "wait":
                await asyncio.sleep(min(5.0, max(0.5, float(action.get("seconds") or 2))))
            elif kind != "read":
                raise ActionError("unknown_action", "That is not an action the browser can take.")
        except ActionError:
            raise
        except Exception as exc:
            raise ActionError("action_failed", f"The browser could not do that on the page ({type(exc).__name__}).") from None
        await self._settle()
        if kind == "click":
            # A click that starts a download: give the browser a moment to begin it (the file is
            # reported with this snapshot or the next one).
            await asyncio.sleep(0.75)
        snapshot = await self.snapshot()
        if self.held:
            return {"outcome": "held_navigation", "held": self.held, "snapshot": snapshot}
        return {"outcome": "done", "snapshot": snapshot}

    async def _settle(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=self.limits.navigation_timeout_ms)
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=2500)

    async def screenshot(self) -> bytes:
        """A JPEG of the viewport with password, card and saved-secret fields and payment frames masked."""
        self.touch()
        for frame in self._frames():
            with contextlib.suppress(Exception):
                await frame.evaluate(MARK_MASKS_JS, [self.mask_attr, self.secret_attr, list(PAYMENT_FRAME_HOSTS)])
        masks = [frame.locator(f"[{self.mask_attr}]") for frame in self._frames()]
        return await self.page.screenshot(type="jpeg", quality=55, mask=masks, mask_color="#3a3a3a", timeout=8000, animations="disabled")

    async def owner_input(self, event: dict[str, Any]) -> None:
        """The owner's own click, text, key or scroll from the live view, as is."""
        self.touch()
        kind = str(event.get("kind") or "")
        if kind == "click":
            x, y = float(event.get("x") or 0), float(event.get("y") or 0)
            if not (0 <= x <= VIEWPORT["width"] and 0 <= y <= VIEWPORT["height"]):
                raise ActionError("bad_input", "The click is outside the page.")
            await self.page.mouse.click(x, y)
        elif kind == "text":
            text = str(event.get("text") or "")[:200]
            await self.page.keyboard.type(text)
        elif kind == "key":
            key = str(event.get("key") or "")
            if key not in OWNER_KEYS:
                raise ActionError("bad_input", "That key is not passed to the page.")
            await self.page.keyboard.press(" " if key == "Space" else key)
        elif kind == "scroll":
            await self.page.mouse.wheel(0, max(-2000, min(2000, float(event.get("dy") or 0))))
        else:
            raise ActionError("bad_input", "That input is not passed to the page.")
        await self._settle()


def _public_action(action: dict[str, Any]) -> dict[str, Any]:
    allowed = {key: action[key] for key in ("type", "ref", "url", "value", "submit", "seconds") if key in action}
    if "value" in allowed:
        allowed["value"] = str(allowed["value"])[:500]
    return allowed


def _masked(item: dict[str, Any]) -> dict[str, Any]:
    """Belt and braces on top of the page script: card and code fields never show a value."""
    if item.get("value") and (is_card_field(item) or is_otp_field(item) or item.get("type") == "password") and not str(item["value"]).startswith("{{"):
        item["value"] = "(hidden)"
    return item


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _without_query(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:300]


__all__ = ["ActionError", "ActionSession", "SessionLimits", "fingerprint"]
