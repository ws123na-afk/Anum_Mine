"""The ``web_search`` tool (anum_api/web_search.py) and its path through the runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall, ToolPolicy, default_tool_registry
from anum_api.governance import governance_store
from anum_api.integrations import IntegrationStatus, default_integration_registry, searxng_integration
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.settings import Settings, settings
from anum_api.store import store
from anum_api.web_search import (
    SearxngUrlError,
    SearxngWebSearch,
    configured_web_search_handler,
    normalize_searxng_url,
    parse_results,
)

TENANT = "tenant_search"
WORKSPACE = "workspace_search"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_a", roles=["owner"])
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_a", "x-user-roles": "owner"}
# Tenant-wide policy (packs, approval rules) needs the organization administrator role.
ORG_ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}

SEARX_RESULTS = {
    "query": "anum",
    "results": [
        {"title": "First  result", "url": "https://one.example/a", "content": "Snippet\none"},
        {"title": "Script", "url": "javascript:alert(1)", "content": "dropped"},
        {"title": "Data", "url": "data:text/html,hi", "content": "dropped"},
        {"title": "FTP", "url": "ftp://files.example/x", "content": "dropped"},
        {"title": "Duplicate", "url": "https://one.example/a", "content": "dup"},
        {"title": "", "url": "http://two.example/", "content": None},
        {"title": "Third", "url": "https://three.example/", "content": "x" * 5000},
        "not a result",
    ],
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _tool(handler: Callable[[httpx.Request], httpx.Response], **options: object) -> SearxngWebSearch:
    return SearxngWebSearch(base_url="http://searxng:8080/", client=_client(handler), **options)  # type: ignore[arg-type]


def _search(tool: SearxngWebSearch, query: str = "anum agent runtime"):
    return asyncio.run(tool(ToolCall(name="web_search", arguments={"query": query}), CONTEXT))


# Unit: request, parsing, limits ---------------------------------------------------------


def test_queries_the_json_api_and_returns_title_url_snippet() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SEARX_RESULTS)

    result = _search(_tool(handler, max_results=5, safe_search=2))

    (request,) = seen
    assert request.method == "GET"
    assert str(request.url).startswith("http://searxng:8080/search?")
    assert request.url.params["q"] == "anum agent runtime"
    assert request.url.params["format"] == "json"
    assert request.url.params["safesearch"] == "2"
    assert request.headers["accept-encoding"] == "identity"
    # No tenant or user identity leaves for the search engines.
    assert TENANT not in str(request.url) and "x-anum-tenant-id" not in request.headers

    assert result.status == "succeeded"
    results = result.output["results"]
    assert [item["url"] for item in results] == [
        "https://one.example/a",
        "http://two.example/",
        "https://three.example/",
    ]
    assert results[0] == {"title": "First result", "url": "https://one.example/a", "snippet": "Snippet one"}
    assert results[1]["title"] == "http://two.example/" and results[1]["snippet"] == ""
    assert len(results[2]["snippet"]) == 600
    assert result.summary.startswith("Found 3 web results:")
    assert result.audit_metadata["result_count"] == 3
    assert result.audit_metadata["query_length"] == len("anum agent runtime")


def test_scheme_filter_keeps_http_and_https_only() -> None:
    results, dropped = parse_results(SEARX_RESULTS, 20)
    assert {item["url"].split(":", 1)[0] for item in results} == {"http", "https"}
    assert dropped == 4  # javascript:, data:, ftp:, and the non-object entry


def test_result_cap() -> None:
    many = {"results": [{"title": str(i), "url": f"https://r{i}.example/"} for i in range(30)]}
    result = _search(_tool(lambda _: httpx.Response(200, json=many), max_results=2))
    assert len(result.output["results"]) == 2


def test_timeout_fails_the_call_with_a_clear_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = _search(_tool(handler, timeout_seconds=3))
    assert result.status == "failed"
    assert result.summary == "Web search timed out after 3 seconds."
    assert result.audit_metadata["outcome"] == "timeout"


def test_overall_deadline_covers_a_body_that_never_ends() -> None:
    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[override]
            yield b'{"results": ['
            await asyncio.sleep(10)
            yield b"]}"

    tool = _tool(lambda _: httpx.Response(200, stream=Endless()), timeout_seconds=0.2)
    result = _search(tool)
    assert result.status == "failed" and "timed out" in result.summary


def test_http_errors_bad_json_and_oversized_bodies_fail_closed() -> None:
    assert _search(_tool(lambda _: httpx.Response(429))).summary == "The search service answered HTTP 429."
    bad = _search(_tool(lambda _: httpx.Response(200, text="<html>")))
    assert bad.status == "failed" and bad.audit_metadata["outcome"] == "bad_response"
    big = {"results": [{"title": "x" * 2000, "url": "https://big.example/"}] * 10}
    oversized = _search(_tool(lambda _: httpx.Response(200, json=big), max_response_bytes=1024))
    assert oversized.status == "failed" and oversized.audit_metadata["outcome"] == "bad_response"

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert _search(_tool(unreachable)).summary == "The search service is unreachable (ConnectError)."


def test_empty_and_overlong_queries_are_refused_without_a_request() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert _search(_tool(handler), "   ").status == "blocked"
    assert _search(_tool(handler), "x" * 401).status == "blocked"


def test_workspace_switch_off_is_respected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    result = _search(_tool(handler, enabled=lambda context: context.workspace_id != WORKSPACE))
    assert result.status == "blocked" and "turned off for this workspace" in result.summary


def test_the_address_is_operator_configuration_only() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"results": []})

    call = ToolCall(
        name="web_search",
        arguments={"query": "q", "url": "http://169.254.169.254/", "base_url": "http://evil.example"},
    )
    asyncio.run(_tool(handler)(call, CONTEXT))
    assert seen and all(url.startswith("http://searxng:8080/search?") for url in seen)


@pytest.mark.parametrize(
    "value",
    ["searxng:8080", "file:///etc/passwd", "http://user:pw@searxng:8080", "http://searxng:8080/?x=1", "ftp://h/"],
)
def test_malformed_searxng_urls_are_refused(value: str) -> None:
    with pytest.raises(SearxngUrlError):
        normalize_searxng_url(value)
    assert configured_web_search_handler(Settings(searxng_url=value)) is None


# Disabled: hidden tool, clear health --------------------------------------------------


def test_empty_url_hides_the_tool_and_health_says_how_to_enable_it() -> None:
    assert configured_web_search_handler(Settings(searxng_url="")) is None
    registry = default_tool_registry(None, None)
    assert "web_search" not in registry.names
    # Without the tool, the web-search skill cannot be selected.
    selected = default_skill_registry().select("search the web for news", registry.names)
    assert "anum.web-search" not in {skill.id for skill in selected}

    (health,) = [
        item
        for item in asyncio.run(default_integration_registry(Settings(searxng_url="")).health())
        if item.id == "searxng"
    ]
    assert health.status == IntegrationStatus.DISABLED
    assert "ANUM_SEARXNG_URL" in health.detail and health.kind == "search"


def test_malformed_url_health_explains_the_problem() -> None:
    health = asyncio.run(
        default_integration_registry(Settings(searxng_url="searxng:8080")).health()
    )
    (item,) = [entry for entry in health if entry.id == "searxng"]
    assert item.status == IntegrationStatus.DISABLED and "absolute http(s) URL" in item.detail


def test_configured_url_registers_a_low_risk_read_only_tool() -> None:
    handler = configured_web_search_handler(Settings(searxng_url="http://searxng:8080"))
    registry = default_tool_registry(None, handler)
    definition = registry.definition("web_search")
    assert definition is not None
    assert definition.risk_level == RiskLevel.LOW and definition.idempotent
    assert definition.target == "searxng"
    decision = ToolPolicy(registry.names).evaluate(ToolCall(name="web_search"), definition, CONTEXT)
    assert decision.outcome.value == "allow"
    definition_item = searxng_integration(Settings(searxng_url="http://searxng:8080/"))
    assert definition_item.configured and definition_item.endpoint == "http://searxng:8080"


# Runtime mediation: the normal task path ----------------------------------------------


@pytest.fixture
def searxng(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=SEARX_RESULTS)

    configured = settings.model_copy(update={"searxng_url": "http://searxng:8080"})
    integrations = default_integration_registry(configured)
    tool = SearxngWebSearch(
        base_url=configured.searxng_url,
        client=_client(handler),
        enabled=integrations.workspace_enabled("searxng"),
    )
    monkeypatch.setattr(main, "integration_registry", integrations)
    monkeypatch.setattr(main, "tool_registry", default_tool_registry(None, tool))
    governance_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield requests
    governance_store.clear()


def _run(client: TestClient, prompt: str) -> dict:
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Search", "prompt": prompt})
    assert task.status_code == 201, task.text
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=OWNER)
    assert started.status_code == 200, started.text
    return started.json()


def _audits() -> list:
    return [record for record in store.audit_records if record.action == "tool.call"]


def test_a_search_task_runs_through_policy_and_is_audited(searxng: list[httpx.Request]) -> None:
    client = TestClient(main.app)
    prompt = "Search the web for the SearXNG JSON API"
    body = _run(client, prompt)

    assert body["approval"] is None
    assert body["task"]["status"] == "completed"
    proposal = next(step for step in body["run"]["steps"] if step["type"] == "tool_proposal")
    assert proposal["metadata"]["tool"] == "web_search"
    assert proposal["metadata"]["policy_outcome"] == "allow"
    assert proposal["metadata"]["risk_level"] == "low"
    assert "anum.web-search" in body["run"]["checkpoint"]["selected_skills"]
    assert body["run"]["result"].startswith("Found 3 web results:")
    assert len(searxng) == 1 and searxng[0].url.params["q"] == prompt

    (audit,) = _audits()
    assert audit.target == "web_search" and audit.outcome == "succeeded"
    assert audit.tenant_id == TENANT and audit.workspace_id == WORKSPACE
    assert audit.metadata["query_length"] == len(prompt)
    assert audit.metadata["result_count"] == 3
    # Counts only: neither the query text nor the results are in the audit log.
    flattened = repr(dict(audit.metadata))
    assert "SearXNG JSON API" not in flattened and "one.example" not in flattened


def test_a_policy_pack_can_deny_web_search(searxng: list[httpx.Request]) -> None:
    client = TestClient(main.app)
    pack = client.post(
        "/api/v1/policy-packs",
        headers=ORG_ADMIN,
        json={"name": "No web", "description": "", "rules": [{"action": "web_search", "effect": "deny"}]},
    )
    assert pack.status_code == 201, pack.text
    assert client.post(f"/api/v1/policy-packs/{pack.json()['id']}/activate", headers=ORG_ADMIN).status_code == 200
    body = _run(client, "Search the web for anything")
    assert body["task"]["status"] == "failed"
    assert searxng == [] and _audits() == []


def test_an_approval_rule_can_require_approval_for_web_search(searxng: list[httpx.Request]) -> None:
    client = TestClient(main.app)
    rule = client.post(
        "/api/v1/organization/approval-rules",
        headers=ORG_ADMIN,
        json={"name": "Review searches", "action_pattern": "web_search"},
    )
    assert rule.status_code == 201, rule.text
    body = _run(client, "Search the web for anything")
    assert body["task"]["status"] == "waiting_approval"
    assert body["approval"]["action"] == "web_search" and body["approval"]["target"] == "searxng"
    assert searxng == []


def test_a_workspace_owner_can_switch_web_search_off(searxng: list[httpx.Request]) -> None:
    client = TestClient(main.app)
    saved = client.put("/api/v1/integrations/searxng/configuration", headers=OWNER, json={"enabled": False})
    assert saved.status_code == 200, saved.text
    health = client.get("/api/v1/integrations", headers=OWNER).json()
    item = next(entry for entry in health if entry["id"] == "searxng")
    assert item["status"] == "disabled" and "this workspace" in item["detail"]

    body = _run(client, "Search the web for anything")
    assert body["task"]["status"] == "failed"
    assert "turned off for this workspace" in body["run"]["steps"][-1]["summary"]
    assert searxng == []
    (audit,) = _audits()
    assert audit.outcome == "blocked" and audit.metadata["outcome"] == "workspace_disabled"
