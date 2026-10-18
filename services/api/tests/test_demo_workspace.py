"""Sales demo workspace (docs/demo-workspace.md): seed, reset, tenant isolation and sending off."""

from __future__ import annotations

import asyncio

import httpx
from pydantic import SecretStr
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api.agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from anum_api.business.store import Query, business_store, open_business_store
from anum_api.calendar_api import calendar_backend
from anum_api import demo as demo_module
from anum_api.demo import demo_store, demo_workspace_id, open_demo_store, send_refusal
from anum_api.developer_github import GitHubClient, GitHubError
from anum_api.email_inbox import email_backend, open_email_store
from anum_api.files import file_store, open_file_metadata_store
from anum_api.knowledge_base import knowledge_store
from anum_api.main import app, store
from anum_api.marketing.store import marketing_store
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.settings import settings

TENANT_A, TENANT_B = "tenant_demo_a", "tenant_demo_b"


def headers(tenant: str, workspace: str, user: str = "owner_1", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


def _clear() -> None:
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()
    for backend in (demo_store, business_store, calendar_backend, email_backend, knowledge_store, marketing_store, file_store):
        backend.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    test_client = TestClient(app)
    for tenant in (TENANT_A, TENANT_B):
        owner = headers(tenant, f"main_{tenant}")
        assert test_client.post("/api/v1/tenants", headers=owner, json={"name": tenant}).status_code == 201
        assert test_client.post("/api/v1/workspaces", headers=owner, json={"name": "Main"}).status_code == 201
        assert test_client.post("/api/v1/workspace-memberships/current", headers=owner).status_code == 201
    try:
        yield test_client
    finally:
        _clear()


def demo_context(tenant: str) -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=demo_workspace_id(tenant), user_id="owner_1", roles=["owner"])


def test_owner_creates_the_demo_workspace_with_tagged_sample_data(client: TestClient) -> None:
    owner = headers(TENANT_A, f"main_{TENANT_A}")
    before = client.get("/api/v1/demo", headers=owner)
    assert before.status_code == 200 and before.json()["exists"] is False

    response = client.post("/api/v1/demo/reset", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True and body["name"] == "Demo" and body["sends_blocked"] is True
    for kind in ("tasks", "approvals", "email_accounts", "calendar_events", "business_accounts", "business_leads",
                 "business_proposals", "marketing_campaigns", "marketing_posts", "files", "kb_collections"):
        assert body["counts"].get(kind, 0) > 0, kind

    demo = headers(TENANT_A, body["workspace_id"])
    tasks = client.get("/api/v1/tasks", headers=demo).json()
    items = tasks["items"] if isinstance(tasks, dict) else tasks
    assert items and all(task["title"].startswith("Demo: ") for task in items)
    approvals = client.get("/api/v1/approvals", headers=demo).json()
    pending = approvals["items"] if isinstance(approvals, dict) else approvals
    assert any(a["status"] == "pending" for a in pending)
    with open_business_store(demo_context(TENANT_A)) as business:
        accounts, total = business.find("accounts", Query())
    assert total == 3 and all(a.name.startswith("Demo: ") for a in accounts)
    # The caller became an owner of the Demo workspace.
    assert client.get("/api/v1/workspaces/current/permissions", headers=demo).json()["can_manage_workspace"] is True


def test_reset_wipes_only_demo_records_and_refills(client: TestClient) -> None:
    owner = headers(TENANT_A, f"main_{TENANT_A}")
    first = client.post("/api/v1/demo/reset", headers=owner).json()
    demo = headers(TENANT_A, first["workspace_id"])
    # A record somebody added to the Demo workspace by hand.
    mine = client.post("/api/v1/calendar/events", headers=demo, json={
        "title": "My own meeting", "starts_at": "2030-01-02T09:00:00Z", "ends_at": "2030-01-02T10:00:00Z"})
    assert mine.status_code == 201, mine.text
    with open_demo_store(demo_context(TENANT_A)) as tags:
        first_ids = {record_id for _, record_id, _ in tags.tagged()}

    second = client.post("/api/v1/demo/reset", headers=owner)
    assert second.status_code == 200
    assert second.json()["records"] == first["records"]
    with open_demo_store(demo_context(TENANT_A)) as tags:
        second_ids = {record_id for _, record_id, _ in tags.tagged()}
    # Fresh records; only the never-connected social account is kept.
    assert len(first_ids & second_ids) == 1
    events = client.get("/api/v1/calendar/events", headers=demo,
                        params={"start": "2029-12-01T00:00:00Z", "end": "2030-02-01T00:00:00Z"})
    assert events.status_code == 200, events.text
    assert [e["title"] for e in events.json()["items"]] == ["My own meeting"]
    # Old demo files are gone; only the new seed's files remain.
    with open_file_metadata_store(demo_context(TENANT_A)) as files:
        assert len(files.list_files(demo_context(TENANT_A), 100)) == 3
    with open_email_store(demo_context(TENANT_A)) as email:
        assert len(email.list_accounts()) == 1
    # Old demo tasks are archived, not active.
    tasks = client.get("/api/v1/tasks", headers=demo).json()
    items = tasks["items"] if isinstance(tasks, dict) else tasks
    assert len(items) == first["counts"]["tasks"]


def test_demo_records_are_isolated_per_tenant(client: TestClient) -> None:
    a = client.post("/api/v1/demo/reset", headers=headers(TENANT_A, f"main_{TENANT_A}")).json()
    b_status = client.get("/api/v1/demo", headers=headers(TENANT_B, f"main_{TENANT_B}")).json()
    assert b_status["exists"] is False and b_status["workspace_id"] != a["workspace_id"]
    b = client.post("/api/v1/demo/reset", headers=headers(TENANT_B, f"main_{TENANT_B}")).json()
    assert b["records"] == a["records"]

    # Tenant B cannot see tenant A's demo records, even naming A's demo workspace id.
    spoof = TenantContext(tenant_id=TENANT_B, workspace_id=a["workspace_id"], user_id="owner_1", roles=["owner"])
    with open_demo_store(spoof) as tags:
        assert tags.tagged() == []
    with open_business_store(spoof) as business:
        assert business.find("accounts", Query())[1] == 0

    # Resetting tenant B leaves tenant A's demo records untouched.
    with open_demo_store(demo_context(TENANT_A)) as tags:
        a_ids = {record_id for _, record_id, _ in tags.tagged()}
    client.post("/api/v1/demo/reset", headers=headers(TENANT_B, f"main_{TENANT_B}"))
    with open_demo_store(demo_context(TENANT_A)) as tags:
        assert {record_id for _, record_id, _ in tags.tagged()} == a_ids


def test_only_owners_may_create_or_reset(client: TestClient) -> None:
    for roles in ("member", "viewer"):
        member = headers(TENANT_A, f"main_{TENANT_A}", user="someone", roles=roles)
        assert client.get("/api/v1/demo", headers=member).status_code == 403
        refused = client.post("/api/v1/demo/reset", headers=member)
        assert refused.status_code == 403, refused.text
        assert "owner" in refused.text, refused.text


def test_the_demo_id_prefix_is_reserved(client: TestClient) -> None:
    response = client.post("/api/v1/organization/workspaces", headers=headers(TENANT_A, f"main_{TENANT_A}"),
                           json={"name": "Squat", "workspace_id": demo_workspace_id(TENANT_B)})
    assert response.status_code == 422


def test_external_sends_are_blocked_in_the_demo_workspace(client: TestClient) -> None:
    sent: list[str] = []

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        sent.append(call.name)
        return ToolResult(status="completed", summary="sent")

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="email.send", description="Send", risk_level=RiskLevel.HIGH,
                                     delivery_channel="email"), handler)
    registry.register(ToolDefinition(name="crm.notes.read", description="Read", risk_level=RiskLevel.LOW), handler)
    demo = demo_context(TENANT_A)
    real = TenantContext(tenant_id=TENANT_A, workspace_id=f"main_{TENANT_A}", user_id="owner_1", roles=["owner"])

    blocked = asyncio.run(registry.execute(ToolCall(name="email.send", arguments={"to": "a@example.com"}), demo))
    assert blocked.status == "blocked" and "demo workspace" in blocked.summary
    assert sent == []
    # Reads still run in the demo, and the same send runs in a real workspace.
    assert asyncio.run(registry.execute(ToolCall(name="crm.notes.read"), demo)).status == "completed"
    assert asyncio.run(registry.execute(ToolCall(name="email.send", arguments={"to": "a@example.com"}), real)).status == "completed"
    assert sent == ["crm.notes.read", "email.send"]
    assert send_refusal(real) is None and send_refusal(demo)

    # Routes that send without a tool refuse too.
    workspace = client.post("/api/v1/demo/reset", headers=headers(TENANT_A, f"main_{TENANT_A}")).json()["workspace_id"]
    demo_headers = headers(TENANT_A, workspace)
    brevo = client.post("/api/v1/brevo/drafts/draft_x/test", headers=demo_headers)
    assert brevo.status_code == 403 and "demo workspace" in brevo.text, brevo.text
    whatsapp = client.post("/api/v1/whatsapp/account/test", headers=demo_headers)
    assert whatsapp.status_code == 403 and "demo workspace" in whatsapp.text, whatsapp.text


def test_deciding_a_demo_approval_never_sends(client: TestClient) -> None:
    workspace = client.post("/api/v1/demo/reset", headers=headers(TENANT_A, f"main_{TENANT_A}")).json()["workspace_id"]
    demo = headers(TENANT_A, workspace)
    approvals = client.get("/api/v1/approvals", headers=demo).json()
    items = approvals["items"] if isinstance(approvals, dict) else approvals
    pending = next(a for a in items if a["status"] == "pending")
    decided = client.post(f"/api/v1/approvals/{pending['id']}/approve", headers=demo,
                          json={"payload_hash": pending["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval"]["status"] == "approved"
    # A demo approval has no agent run behind it: approving it records the decision only.
    assert decided.json()["approval"]["run_id"] is None


def test_reads_and_drafts_against_integrations_still_run_in_the_demo() -> None:
    demo = demo_context(TENANT_A)
    allowed = (
        ToolDefinition(name="web_search", description="Search", risk_level=RiskLevel.LOW, target="search.example.com", action_kind="read"),
        ToolDefinition(name="brevo.campaigns.list", description="List", risk_level=RiskLevel.LOW, target="api.brevo.com", action_kind="read"),
        ToolDefinition(name="brevo.campaigns.report", description="Report", risk_level=RiskLevel.LOW, target="api.brevo.com", action_kind="read"),
        ToolDefinition(name="brevo.campaigns.draft", description="Draft", risk_level=RiskLevel.LOW, target="api.brevo.com", action_kind="draft"),
        ToolDefinition(name="email.draft_reply", description="Draft", risk_level=RiskLevel.LOW, action_kind="draft"),
    )
    for definition in allowed:
        assert send_refusal(demo, definition.name, definition, {"to": "a@example.com"}) is None, definition.name
    refused = (
        ToolDefinition(name="brevo.campaigns.send", description="Send", risk_level=RiskLevel.HIGH, target="api.brevo.com", action_kind="publish"),
        ToolDefinition(name="email.send", description="Send", risk_level=RiskLevel.HIGH, delivery_channel="email"),
        ToolDefinition(name="whatsapp.reply", description="Reply", risk_level=RiskLevel.HIGH, target="graph.facebook.com"),
        ToolDefinition(name="commerce.update_price", description="Price", risk_level=RiskLevel.HIGH, target="sellingpartnerapi.amazon.com"),
        ToolDefinition(name="mystery.tool", description="Unknown", risk_level=RiskLevel.MEDIUM),
    )
    for definition in refused:
        assert send_refusal(demo, definition.name, definition, {}), definition.name


def test_github_changes_never_leave_the_demo_workspace(client: TestClient) -> None:
    workspace = client.post("/api/v1/demo/reset", headers=headers(TENANT_A, f"main_{TENANT_A}")).json()["workspace_id"]
    demo = headers(TENANT_A, workspace)
    install = client.post("/api/v1/developer/projects/proj_x/install-workflow", headers=demo)
    assert install.status_code == 403 and "demo workspace" in install.text, install.text
    request = client.post("/api/v1/developer/requests", headers=demo, json={"text": "Add a footer link to the privacy page"})
    assert request.status_code == 403 and "demo workspace" in request.text, request.text

    # The client layer refuses too, so no other route can push, dispatch or merge.
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={})

    github = GitHubClient("owner/repo", SecretStr("example"), transport=httpx.MockTransport(handler), read_only=True)
    with pytest.raises(GitHubError):
        asyncio.run(github.dispatch({"request_id": "dev_x"}))
    with pytest.raises(GitHubError):
        asyncio.run(github.create_branch("anum/x", "a" * 40))
    asyncio.run(github.branch_sha("main"))
    assert calls == ["GET"]


def test_a_seed_that_fails_part_way_is_tagged_and_reset_cleans_it(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = headers(TENANT_A, f"main_{TENANT_A}")
    healthy = client.post("/api/v1/demo/reset", headers=owner).json()

    def broken(*args: object) -> None:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(demo_module, "_seed_marketing", broken)
    failing = TestClient(app, raise_server_exceptions=False)
    assert failing.post("/api/v1/demo/reset", headers=owner).status_code == 500
    # Everything written before the failure is tagged, so nothing is orphaned.
    with open_business_store(demo_context(TENANT_A)) as business:
        accounts, total = business.find("accounts", Query())
    with open_demo_store(demo_context(TENANT_A)) as tags:
        tagged = {record_id for kind, record_id, _ in tags.tagged() if kind == "business_accounts"}
    assert total == 3 and {a.id for a in accounts} == tagged

    monkeypatch.undo()
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    again = client.post("/api/v1/demo/reset", headers=owner)
    assert again.status_code == 200, again.text
    assert again.json()["records"] == healthy["records"]
    with open_business_store(demo_context(TENANT_A)) as business:
        assert business.find("accounts", Query())[1] == 3
    with open_file_metadata_store(demo_context(TENANT_A)) as files:
        assert len(files.list_files(demo_context(TENANT_A), 100)) == 3
