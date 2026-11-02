"""Browser research (docs/browser-research.md): the network guard, the request gate, the service,
the ``web.browse_page`` tool and its path through the runtime's mediation.

None of these tests needs Chromium: the gate is driven directly, the service with a fake
kernel and the tool with a mock service. ``test_browser_research_chromium.py`` (marker
``browser``) runs the same rules through a real Chromium.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteClient

from anum_api import main
from anum_api.agent_planning import choose_tool_call, first_https_address
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall, ToolPolicy, ToolPolicyOutcome, default_tool_registry
from anum_api.browser_research import BROWSE_TOOL, BROWSER_INTEGRATION_ID
from anum_api.browser_research.guard import PinnedFetcher, Refused, RobotsRules, check_page_url, check_request_url
from anum_api.browser_research.kernel import Gate, Limits
from anum_api.browser_research.service import BrowserService, create_app
from anum_api.browser_research.tool import BrowserResearchTool, browse_definition, register_browser_tools, service_problem
from anum_api.chat_tools import chat_intent, items_from_result
from anum_api.agent_tools import ToolResult
from anum_api.governance import governance_store
from anum_api.integrations import default_integration_registry
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.store import store

TENANT = "tenant_browse"
WORKSPACE = "workspace_browse"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_b", roles=["owner"])
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_b", "x-user-roles": "owner"}
ORG_ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}
TOKEN = "browser-service-token-for-tests-only-example"
PUBLIC = {"site.example": "93.184.215.14", "cdn.example": "93.184.215.15"}


# -- a fake internet: DNS answers and a server that records which address it was dialled on ----


class Net:
    def __init__(self) -> None:
        self.dns: dict[str, list[list[str]]] = {host: [[address]] for host, address in PUBLIC.items()}
        self.lookups: list[str] = []
        self.dialled: list[tuple[str, str, str]] = []  # (Host header, dialled address, path)
        self.pages: dict[tuple[str, str], httpx.Response] = {}

    async def resolve(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        answers = self.dns.get(host)
        if not answers:
            raise OSError("no such host")
        # Each lookup takes the next answer; the last one repeats (a rebinding name changes).
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.dialled.append((host, request.url.host, request.url.path))
        if request.url.path == "/robots.txt" and (host, "/robots.txt") not in self.pages:
            return httpx.Response(404)
        return self.pages.get((host, request.url.path), httpx.Response(404))

    def fetcher(self) -> PinnedFetcher:
        return PinnedFetcher(resolve=self.resolve, inner=httpx.MockTransport(self.handler), timeout_seconds=5)


def html(body: str, **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", **headers}, text=body)


def gate(net: Net, **limits: Any) -> Gate:
    fetcher = net.fetcher()
    return Gate(fetcher, RobotsRules(fetcher), Limits(**limits))


def answer(g: Gate, url: str, *, method: str = "GET", kind: str = "document", main_frame: bool = True):
    return asyncio.run(g.answer(url=url, method=method, resource_type=kind, main_frame=main_frame))


# -- the address rules -------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://site.example/", "file:///etc/passwd", "data:text/html,<p>x</p>", "javascript:alert(1)",
    "chrome://settings", "about:blank", "ftp://site.example/", "ws://site.example/", "blob:https://site.example/x",
    "https://user:pass@site.example/", "https://site.example/?q=secret", "https://site.example:8443/",
    "https://127.0.0.1/", "https://10.0.0.1/", "https://169.254.169.254/latest/meta-data/", "https://[::1]/",
    "https://[fd00::1]/", "https://[fd00:ec2::254]/", "https://[::ffff:127.0.0.1]/", "https://2130706433/",
    "https://0x7f.1/", "https://192.168.1.1/", "https://100.64.0.1/", "https://[fe80::1]/", "", " ", "https://site.example/a b",
])
def test_page_addresses_outside_public_https_are_refused(url: str) -> None:
    with pytest.raises(Refused):
        check_page_url(url)


def test_page_address_is_normalised_without_fragment() -> None:
    assert check_page_url("https://Site.Example/a#top") == "https://site.example/a"
    assert check_page_url("https://site.example") == "https://site.example/"
    # A request the page makes may carry a query (scripts, fetches), still https and public.
    assert check_request_url("https://cdn.example/app.js?v=3") == "https://cdn.example/app.js?v=3"
    with pytest.raises(Refused):
        check_request_url("http://cdn.example/app.js")


# -- DNS rebinding and pinning -----------------------------------------------------------------


def test_each_request_resolves_once_and_dials_the_checked_address() -> None:
    net = Net()
    net.pages[("site.example", "/")] = html("<p>hi</p>")
    g = gate(net)
    g.begin("https://site.example/")
    status, headers, body = answer(g, "https://site.example/")
    assert status == 200 and body == b"<p>hi</p>"
    # robots.txt then the page: two lookups, both dialled on the address that was checked,
    # with the original Host header (and TLS name) kept.
    assert net.lookups == ["site.example", "site.example"]
    assert net.dialled == [("site.example", "93.184.215.14", "/robots.txt"), ("site.example", "93.184.215.14", "/")]


def test_dns_rebinding_between_robots_and_the_page_is_refused() -> None:
    net = Net()
    # First answer public (robots.txt is read), second answer the metadata address.
    net.dns["site.example"] = [["93.184.215.14"], ["169.254.169.254"]]
    net.pages[("site.example", "/")] = html("<p>secret</p>")
    g = gate(net)
    g.begin("https://site.example/")
    assert answer(g, "https://site.example/") is None
    assert g.refusal is not None and g.refusal.code == "refused_address"
    # The page was never requested from any address: only robots.txt on the public one.
    assert net.dialled == [("site.example", "93.184.215.14", "/robots.txt")]


def test_a_subresource_whose_name_rebinds_to_loopback_is_refused() -> None:
    net = Net()
    net.dns["cdn.example"] = [["93.184.215.15"], ["127.0.0.1"]]
    net.pages[("cdn.example", "/a.js")] = httpx.Response(200, headers={"content-type": "text/javascript"}, text="1")
    g = gate(net)
    assert answer(g, "https://cdn.example/a.js", kind="script", main_frame=False)[0] == 200
    assert answer(g, "https://cdn.example/a.js", kind="script", main_frame=False) is None
    assert g.stats.reasons["refused_address"] == 1
    assert [address for _, address, _ in net.dialled] == ["93.184.215.15"]


@pytest.mark.parametrize("answers", [["93.184.215.14", "10.1.2.3"], ["fd00::5"], ["::ffff:192.168.0.1"], ["64:ff9b::7f00:1"], ["0.0.0.0"]])
def test_a_name_with_any_non_public_answer_is_refused(answers: list[str]) -> None:
    net = Net()
    net.dns["site.example"] = [answers]
    g = gate(net)
    g.begin("https://site.example/")
    assert answer(g, "https://site.example/") is None
    assert g.refusal is not None and g.refusal.code == "refused_address"
    assert net.dialled == []


# -- redirects -----------------------------------------------------------------------------------


@pytest.mark.parametrize("location", [
    "https://169.254.169.254/latest/meta-data/", "https://127.0.0.1/admin", "http://site.example/x", "file:///etc/passwd",
    "https://site.example:8443/", "https://internal.example/",
])
def test_a_page_redirect_to_a_private_or_unsafe_address_is_refused(location: str) -> None:
    net = Net()
    net.dns["internal.example"] = [["10.0.0.7"]]
    net.pages[("site.example", "/")] = httpx.Response(302, headers={"location": location})
    g = gate(net)
    g.begin("https://site.example/")
    result = answer(g, "https://site.example/")
    if g.refusal is None:
        # Syntax was fine: the gate records where to go and the kernel navigates there next,
        # which is refused before any request (the address resolves privately).
        assert result is not None and g.redirect_to == location
        g.begin(location)
        assert answer(g, location) is None
    assert g.refusal is not None and g.refusal.code in {"refused_address", "refused_scheme", "refused_url"}
    assert all(address == "93.184.215.14" for _, address, _ in net.dialled)


def test_a_page_redirect_to_a_public_page_is_followed_as_a_new_navigation() -> None:
    net = Net()
    net.pages[("site.example", "/")] = httpx.Response(301, headers={"location": "/home?lang=en"})
    net.pages[("site.example", "/home")] = html("<p>home</p>")
    g = gate(net)
    g.begin("https://site.example/")
    status, _, body = answer(g, "https://site.example/")
    assert (status, body) == (200, b"") and g.redirect_to == "https://site.example/home?lang=en"
    g.begin(g.redirect_to)
    assert answer(g, "https://site.example/home?lang=en")[2] == b"<p>home</p>"


def test_a_subresource_redirect_to_metadata_is_refused() -> None:
    net = Net()
    net.dns["meta.example"] = [["169.254.169.254"]]
    net.pages[("cdn.example", "/x.js")] = httpx.Response(302, headers={"location": "https://meta.example/latest"})
    g = gate(net)
    assert answer(g, "https://cdn.example/x.js", kind="script", main_frame=False) is None
    assert g.stats.reasons["refused_address"] == 1
    assert [address for _, address, _ in net.dialled] == ["93.184.215.15"]


# -- what the gate never does --------------------------------------------------------------------


def test_writes_frames_images_and_unplanned_navigations_are_refused_without_a_request() -> None:
    net = Net()
    g = gate(net)
    g.begin("https://site.example/")
    # A refused page navigation answers "204 No Content", so the shown page stays in place.
    assert answer(g, "https://site.example/form", method="POST", kind="document") == (204, {}, b"")
    assert answer(g, "https://site.example/api", method="PUT", kind="fetch", main_frame=False) is None
    assert answer(g, "https://site.example/a.png", kind="image", main_frame=False) is None
    assert answer(g, "https://site.example/ping", kind="ping", main_frame=False) is None
    assert answer(g, "https://site.example/frame", kind="document", main_frame=False) is None
    assert answer(g, "https://site.example/elsewhere", kind="document") == (204, {}, b"")  # not the planned navigation
    g.locked = True
    assert answer(g, "https://site.example/", kind="document") == (204, {}, b"")  # after the page loaded
    assert net.dialled == []
    assert g.stats.reasons == {"method": 2, "type:image": 1, "type:ping": 1, "frame": 1, "navigation": 2}


def test_cookies_and_redirect_headers_are_never_handed_to_the_browser() -> None:
    net = Net()
    net.pages[("site.example", "/")] = html("<p>x</p>", **{"set-cookie": "session=1", "content-language": "en", "refresh": "0;url=https://x"})
    g = gate(net)
    g.begin("https://site.example/")
    _, headers, _ = answer(g, "https://site.example/")
    assert headers == {"content-type": "text/html; charset=utf-8", "content-language": "en"}


@pytest.mark.parametrize("response", [
    httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"),
    httpx.Response(200, headers={"content-type": "text/html", "content-disposition": "attachment; filename=x.exe"}, content=b"MZ"),
    httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"\x00"),
])
def test_downloads_and_files_are_not_pages(response: httpx.Response) -> None:
    net = Net()
    net.pages[("site.example", "/file")] = response
    g = gate(net)
    g.begin("https://site.example/file")
    assert answer(g, "https://site.example/file") is None
    assert g.refusal is not None and g.refusal.code == "not_text"


def test_robots_txt_is_obeyed_and_an_unanswered_robots_txt_blocks_the_host() -> None:
    net = Net()
    net.pages[("site.example", "/robots.txt")] = httpx.Response(200, text="User-agent: ANUM-Research\nDisallow: /private\n")
    net.pages[("site.example", "/private/x")] = html("secret")
    g = gate(net)
    g.begin("https://site.example/private/x")
    assert answer(g, "https://site.example/private/x") is None and g.refusal.code == "robots_disallowed"  # type: ignore[union-attr]

    down = Net()
    down.pages[("site.example", "/robots.txt")] = httpx.Response(503)
    g = gate(down)
    g.begin("https://site.example/")
    assert answer(g, "https://site.example/") is None and g.refusal.code == "robots_unavailable"  # type: ignore[union-attr]


def test_request_count_and_size_limits() -> None:
    net = Net()
    net.pages[("cdn.example", "/a.js")] = httpx.Response(200, headers={"content-type": "text/javascript"}, content=b"x" * 600)
    g = gate(net, max_requests=2, max_total_bytes=1000)
    assert answer(g, "https://cdn.example/a.js", kind="script", main_frame=False) is not None
    assert answer(g, "https://cdn.example/a.js", kind="script", main_frame=False) is None  # would pass 1000 bytes
    assert answer(g, "https://cdn.example/a.js", kind="script", main_frame=False) is None  # third request
    assert g.stats.reasons["too_large"] == 1 and g.stats.reasons["too_many_requests"] == 1


def test_compressed_answers_are_refused() -> None:
    net = Net()
    net.pages[("cdn.example", "/a.js")] = httpx.Response(200, headers={"content-type": "text/javascript", "content-encoding": "gzip"}, content=b"x")
    g = gate(net)
    assert answer(g, "https://cdn.example/a.js", kind="script", main_frame=False) is None
    assert g.stats.reasons["compressed"] == 1


# -- the browser service -------------------------------------------------------------------------


def _service(browse_fn: Callable[..., Any], *, executable: str | None = "/bin/sh") -> StarletteClient:
    return StarletteClient(create_app(BrowserService(TOKEN, executable_path=executable, browse_fn=browse_fn)))


def test_the_service_needs_its_token_and_a_well_formed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anum_api.browser_research.service.chromium_available", lambda _path: (True, "ok"))
    calls: list[str] = []

    async def fake(url: str, **_: Any) -> dict[str, Any]:
        calls.append(url)
        return {"outcome": "succeeded", "url": url, "final_url": url, "title": "T", "text": "t", "links": [], "stats": {"requests": 1}}

    client = _service(fake)
    assert client.post("/v1/browse", json={"url": "https://site.example/"}).status_code == 401
    assert client.post("/v1/browse", json={"url": "https://site.example/"}, headers={"authorization": "Bearer wrong"}).status_code == 401
    auth = {"authorization": f"Bearer {TOKEN}"}
    assert client.post("/v1/browse", json={"link": "x"}, headers=auth).status_code == 422
    assert client.post("/v1/browse", content=b"x" * 5000, headers=auth).status_code == 413
    ok = client.post("/v1/browse", json={"url": "https://site.example/"}, headers=auth)
    assert ok.status_code == 200 and ok.json()["outcome"] == "succeeded" and calls == ["https://site.example/"]
    with pytest.raises(ValueError):
        BrowserService("short")


def test_the_service_says_honestly_when_chromium_is_missing() -> None:
    async def never(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("must not browse")

    client = _service(never, executable="/nonexistent/chromium")
    health = client.get("/healthz")
    assert health.status_code == 503 and health.json()["detail"].startswith("Not available on this server")
    answer_ = client.post("/v1/browse", json={"url": "https://site.example/"}, headers={"authorization": f"Bearer {TOKEN}"})
    assert answer_.status_code == 503 and answer_.json()["message"].startswith("Not available on this server")


def test_a_browser_crash_is_an_outcome_not_a_stack_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anum_api.browser_research.service.chromium_available", lambda _path: (True, "ok"))

    async def crash(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("Target closed /secret/path")

    body = _service(crash).post("/v1/browse", json={"url": "https://site.example/"}, headers={"authorization": f"Bearer {TOKEN}"}).json()
    assert body["outcome"] == "browser_error" and "secret" not in repr(body)


# -- the tool ------------------------------------------------------------------------------------

PAGE = {
    "outcome": "succeeded", "url": "https://site.example/pricing", "final_url": "https://site.example/pricing",
    "status_code": 200, "title": "Pricing  plans", "text": "Basic 10 AED\nPro 50 AED", "truncated": False,
    "links": [{"url": "https://site.example/contact", "text": "Contact"}, {"url": "javascript:alert(1)", "text": "x"}],
    "stats": {"requests": 3, "blocked": 2, "bytes": 1200, "blocked_reasons": {"type:image": 2}},
}


class Service:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = PAGE if body is None else body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


def _tool(service: Service, **options: Any) -> BrowserResearchTool:
    return BrowserResearchTool(base_url="http://browser:8090/", token=TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(service)), **options)


def _browse(tool: BrowserResearchTool, url: str = "https://site.example/pricing", context: TenantContext = CONTEXT) -> ToolResult:
    return asyncio.run(tool(ToolCall(name=BROWSE_TOOL, arguments={"url": url}), context))


def test_the_tool_reads_through_the_service_and_audits_addresses_only() -> None:
    service = Service()
    result = _browse(_tool(service))
    assert result.status == "succeeded"
    assert result.output["title"] == "Pricing plans" and result.output["links"] == [{"url": "https://site.example/contact", "text": "Contact"}]
    (request,) = service.requests
    assert request.url == "http://browser:8090/v1/browse" and request.headers["authorization"] == f"Bearer {TOKEN}"
    audit = result.audit_metadata
    assert audit["url"] == "https://site.example/pricing" and audit["final_host"] == "site.example" and audit["outcome"] == "succeeded"
    assert audit["requests"] == 3 and audit["blocked"] == 2 and audit["blocked_reasons"] == {"type:image": 2}
    assert "Pricing" not in repr(audit) and "AED" not in repr(audit) and "contact" not in repr(audit)


def test_refused_addresses_never_reach_the_service() -> None:
    service = Service()
    for url in ("http://site.example/", "https://169.254.169.254/", "file:///etc/passwd", "https://site.example/?token=x"):
        result = _browse(_tool(service), url)
        assert result.status == "blocked"
    assert service.requests == []


def test_not_available_on_this_server_when_the_service_is_not_set_up() -> None:
    for url, token in (("", ""), ("http://browser:8090", ""), ("ftp://browser", TOKEN), ("http://browser:8090", "short")):
        assert service_problem(url, token) is not None
        tool = BrowserResearchTool(base_url=url, token=token)
        result = _browse(tool)
        assert result.status == "failed" and result.summary.startswith("Not available on this server")
        assert result.audit_metadata["outcome"] == "unavailable"


def test_service_answers_map_to_clear_failures() -> None:
    missing = _browse(_tool(Service(503, {"outcome": "unavailable", "message": "Not available on this server: Chromium is not installed in this image."})))
    assert missing.summary == "Not available on this server: Chromium is not installed in this image."
    assert _browse(_tool(Service(429, {}))).audit_metadata["outcome"] == "busy"
    assert _browse(_tool(Service(401, {}))).audit_metadata["outcome"] == "service_unauthorised"
    refused = _browse(_tool(Service(200, {"outcome": "refused_address", "message": "That address is not a public internet site, so it is not read.", "final_url": "https://meta.example/", "stats": {}})))
    assert refused.status == "blocked" and refused.audit_metadata["final_host"] == "meta.example"
    login = _browse(_tool(Service(200, {"outcome": "login_required", "message": "behind a login", "stats": {}})))
    assert login.status == "blocked" and login.output == {}

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    tool = BrowserResearchTool(base_url="http://browser:8090", token=TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    assert _browse(tool).audit_metadata["outcome"] == "service_unreachable"


def test_the_workspace_switch_and_the_hourly_cap() -> None:
    service = Service()
    off = _browse(_tool(service, enabled=lambda _context: False))
    assert off.status == "blocked" and "off for this workspace" in off.summary and service.requests == []
    now = [0.0]
    capped = _tool(service, per_hour=2, clock=lambda: now[0])
    assert [_browse(capped).status for _ in range(3)] == ["succeeded", "succeeded", "failed"]
    other = TenantContext(tenant_id=TENANT, workspace_id="other_workspace", user_id="u", roles=["owner"])
    assert _browse(capped, context=other).status == "succeeded"  # the cap is per workspace
    now[0] = 3601.0
    assert _browse(capped).status == "succeeded"


def test_the_definition_is_low_risk_read_only_and_role_gated() -> None:
    definition = browse_definition()
    assert definition.risk_level.value == "low" and definition.action_kind == "read" and definition.idempotent
    policy = ToolPolicy()
    call = ToolCall(name=BROWSE_TOOL, arguments={"url": "https://site.example/"})
    assert policy.evaluate(call, definition, CONTEXT).outcome == ToolPolicyOutcome.ALLOW
    viewer = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="v", roles=["viewer"])
    assert policy.evaluate(call, definition, viewer).outcome == ToolPolicyOutcome.BLOCK


# -- planning and chat ---------------------------------------------------------------------------


def test_the_address_comes_from_the_users_own_words() -> None:
    assert first_https_address("Open https://site.example/pricing. Thanks") == "https://site.example/pricing"
    assert first_https_address("افتح https://site.example/ar، شكرا") == "https://site.example/ar"
    assert first_https_address("open http://site.example/") is None
    skills = default_skill_registry()
    tools = {BROWSE_TOOL, "web_search", "anum.respond"}
    prompt = "Open https://site.example/pricing and read the page"
    call = choose_tool_call(prompt, skills.select(prompt, tools), "the model says https://evil.example/", tools)
    assert call == ToolCall(name=BROWSE_TOOL, arguments={"url": "https://site.example/pricing"})
    # No address in the prompt: browsing is not planned, whatever the model wrote.
    other = "Open the web and search for pricing"
    assert choose_tool_call(other, skills.select(other, tools), "https://evil.example/", tools).name == "web_search"
    assert chat_intent(prompt, skills, tools) == "web"


def test_chat_card_lists_the_page_and_its_links_as_sources() -> None:
    result = ToolResult(status="succeeded", summary="", output={**PAGE, "links": PAGE["links"]})
    items = items_from_result(BROWSE_TOOL, result)
    assert [item.url for item in items] == ["https://site.example/pricing", "https://site.example/contact"]
    assert items[0].badges == ["source"] and items[1].badges == ["link"]


# -- the integration and the runtime path --------------------------------------------------------


def test_the_integration_is_off_per_workspace_until_an_owner_turns_it_on() -> None:
    unconfigured = default_integration_registry(settings.model_copy(update={"browser_research_url": "", "browser_research_token": ""}))
    entry = next(item for item in asyncio.run(unconfigured.health()) if item.id == BROWSER_INTEGRATION_ID)
    assert entry.status == "disabled" and not entry.available and entry.detail.startswith("Not available on this server")


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[Service]:
    service = Service()
    configured = settings.model_copy(update={"browser_research_url": "http://browser:8090", "browser_research_token": TOKEN})
    integrations = default_integration_registry(configured)
    registry = default_tool_registry(None, None)
    tool = BrowserResearchTool(
        base_url=configured.browser_research_url, token=TOKEN,
        client=httpx.AsyncClient(transport=httpx.MockTransport(service)),
        enabled=integrations.workspace_enabled(BROWSER_INTEGRATION_ID),
    )
    registry.register(browse_definition(), tool)
    monkeypatch.setattr(main, "integration_registry", integrations)
    monkeypatch.setattr(main, "tool_registry", registry)
    governance_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield service
    governance_store.clear()


def _run(client: TestClient, prompt: str) -> dict:
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Read a page", "prompt": prompt})
    assert task.status_code == 201, task.text
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=OWNER)
    assert started.status_code == 200, started.text
    return started.json()


def _audits() -> list:
    return [record for record in store.audit_records if record.action == "tool.call"]


PROMPT = "Open https://site.example/pricing and read the page"


def test_off_by_default_then_an_owner_turns_it_on_and_a_task_reads_the_page(browser: Service) -> None:
    client = TestClient(main.app)
    health = client.get("/api/v1/integrations", headers=OWNER).json()
    entry = next(item for item in health if item["id"] == BROWSER_INTEGRATION_ID)
    assert entry["status"] == "disabled" and entry["available"] and not entry["enabled"]
    assert entry["detail"] == "Off for this workspace until an owner turns it on."
    assert entry["remediation"] == "A workspace owner turns it on here, under Integrations."
    assert client.get(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=OWNER).json()["enabled"] is False

    body = _run(client, PROMPT)
    assert body["task"]["status"] == "failed" and browser.requests == []
    (audit,) = _audits()
    assert audit.outcome == "blocked" and audit.metadata["outcome"] == "workspace_disabled"

    member = {**OWNER, "x-user-id": "member_b", "x-user-roles": "member"}
    assert client.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=member, json={"enabled": True}).status_code == 403
    assert client.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=OWNER, json={"enabled": True}).status_code == 200

    store.audit_records.clear()
    body = _run(client, PROMPT)
    assert body["task"]["status"] == "completed", body["run"]["steps"][-1]
    proposal = next(step for step in body["run"]["steps"] if step["type"] == "tool_proposal")
    assert proposal["metadata"]["tool"] == BROWSE_TOOL and proposal["metadata"]["policy_outcome"] == "allow"
    assert "anum.browser-research" in body["run"]["checkpoint"]["selected_skills"]
    assert len(browser.requests) == 1
    (audit,) = _audits()
    assert audit.target == BROWSE_TOOL and audit.outcome == "succeeded"
    assert audit.tenant_id == TENANT and audit.workspace_id == WORKSPACE
    assert audit.metadata["url"] == "https://site.example/pricing"
    assert "AED" not in repr(dict(audit.metadata)) and "Pricing plans" not in repr(dict(audit.metadata))


def test_a_policy_pack_can_deny_browsing(browser: Service) -> None:
    client = TestClient(main.app)
    client.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=OWNER, json={"enabled": True})
    pack = client.post("/api/v1/policy-packs", headers=ORG_ADMIN, json={"name": "No browsing", "description": "", "rules": [{"action": "web.browse_page", "effect": "deny"}]})
    assert pack.status_code == 201, pack.text
    assert client.post(f"/api/v1/policy-packs/{pack.json()['id']}/activate", headers=ORG_ADMIN).status_code == 200
    assert _run(client, PROMPT)["task"]["status"] == "failed"
    assert browser.requests == []


def test_an_approval_rule_can_require_approval_for_browsing(browser: Service) -> None:
    client = TestClient(main.app)
    client.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=OWNER, json={"enabled": True})
    rule = client.post("/api/v1/organization/approval-rules", headers=ORG_ADMIN, json={"name": "Review browsing", "action_pattern": "web.browse_page"})
    assert rule.status_code == 201, rule.text
    body = _run(client, PROMPT)
    assert body["task"]["status"] == "waiting_approval"
    assert body["approval"]["action"] == BROWSE_TOOL and body["approval"]["arguments"] == {"url": "https://site.example/pricing"}
    assert browser.requests == []


def test_the_api_and_worker_registries_carry_the_tool() -> None:
    assert BROWSE_TOOL in main.tool_registry.names
    registry = register_browser_tools(default_tool_registry(None, None), settings)
    assert registry.definition(BROWSE_TOOL) is not None
    # Registering twice is harmless (the worker and the API share helpers).
    register_browser_tools(registry, settings)


def test_chromium_availability_is_reported_in_words_an_operator_can_act_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import sys
    import types

    from anum_api.browser_research.kernel import chromium_available

    monkeypatch.setitem(sys.modules, "playwright", None)  # not installed
    monkeypatch.delitem(sys.modules, "playwright.async_api", raising=False)
    assert chromium_available() == (False, "Not available on this server: the Playwright package is not installed in this image.")

    fake = types.ModuleType("playwright.async_api")
    fake.async_playwright = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert chromium_available() == (False, "Not available on this server: Chromium is not installed in this image.")
    (tmp_path / "chromium_headless_shell-1194").mkdir()
    assert chromium_available() == (True, "Chromium is installed.")
    assert chromium_available(str(tmp_path / "missing"))[0] is False
