"""Department assistants, customer organisations and the tenant switcher (in memory).

docs/team-work.md#department-assistants and docs/customer-organisations.md.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import dependencies
from anum_api.agent_tools import ToolCall, ToolPolicyOutcome
from anum_api.dependencies import memory_repository
from anum_api.main import app, store
from anum_api.organisations import memory_branding
from anum_api.runtime import AgentRuntime
from anum_api.schemas import Task, TaskStatus, TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.team_store import memory_team_store

from test_identity import FakeJwksEndpoint, SigningKey, bearer, make_validator

TENANT = "tenant_ops"
WORKSPACE = "workspace_ops"


def h(user: str, roles: str = "member", tenant: str = TENANT, workspace: str = WORKSPACE, **extra: str) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles, **extra}


OWNER = h("owner_1", "owner")
OPERATOR = h("owner_1", "owner,platform_operator")
MEMBER = h("member_1")


def _clear() -> None:
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()
    memory_team_store.clear()
    memory_branding.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    test_client = TestClient(app)
    assert test_client.post("/api/v1/tenants", headers=OWNER, json={"name": "Platform Co"}).status_code == 201
    assert test_client.post("/api/v1/workspaces", headers=OWNER, json={"name": "HQ"}).status_code == 201
    assert test_client.post("/api/v1/workspace-memberships/current", headers=OWNER).status_code == 201
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_1", role="member", created_at=now, updated_at=now)
    )
    try:
        yield test_client
    finally:
        _clear()


def new_assistant(client: TestClient, **body) -> dict:
    payload = {"department": "sales", "name": "Sales assistant", "allowed_tools": ["anum.respond", "team.assign_task"],
               "routing_keywords": ["price", "quote", "سعر"], "autonomy": "suggest", **body}
    response = client.post("/api/v1/assistants", headers=OWNER, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- assistants


def test_templates_cover_the_five_departments_with_registered_tools_only(client: TestClient) -> None:
    templates = client.get("/api/v1/assistants/templates", headers=MEMBER).json()
    assert [t["department"] for t in templates] == ["sales", "support", "operations", "finance", "tyre_engineering"]
    from anum_api.main import tool_registry

    assert all(set(t["allowed_tools"]) <= tool_registry.names for t in templates)
    assert next(t for t in templates if t["department"] == "tyre_engineering")["autonomy"] == "observe"


def test_only_owners_manage_assistants_and_tools_are_checked(client: TestClient) -> None:
    denied = client.post("/api/v1/assistants", headers=MEMBER, json={"department": "sales", "name": "X"})
    assert denied.status_code == 403 and "Only workspace owners" in denied.json()["error"]["message"]
    bad = client.post("/api/v1/assistants", headers=OWNER, json={"department": "sales", "name": "X", "allowed_tools": ["shell.exec"]})
    assert bad.status_code == 422 and "shell.exec" in bad.json()["error"]["message"]
    created = new_assistant(client)
    assert client.post("/api/v1/assistants", headers=OWNER, json={"department": "sales", "name": "sales ASSISTANT"}).status_code == 409
    updated = client.put(f"/api/v1/assistants/{created['id']}", headers=OWNER,
                         json={**{k: created[k] for k in ("department", "name", "instructions", "allowed_tools", "routing_keywords")}, "autonomy": "observe", "enabled": True})
    assert updated.status_code == 200 and updated.json()["autonomy"] == "observe"
    assert client.get("/api/v1/assistants", headers=MEMBER).json()[0]["id"] == created["id"]
    feed = [a["summary"] for a in client.get("/api/v1/team/activity", headers=MEMBER).json()["items"]]
    assert "Sales assistant was set up (suggest)" in feed


def test_inbound_messages_are_routed_by_keyword_explicitly_or_left_for_triage(client: TestClient) -> None:
    sales = new_assistant(client)
    support = new_assistant(client, department="support", name="Support assistant", routing_keywords=["return", "refund"])
    routed = client.post("/api/v1/assistants/route", headers=MEMBER, json={
        "channel": "whatsapp", "sender": "+966500000000", "subject": "", "body": "What is your price for 4 tyres? Need a quote today",
    })
    assert routed.status_code == 201, routed.text
    body = routed.json()
    assert body["assistant"]["id"] == sales["id"] and body["matched_keywords"] == ["price", "quote"]
    item = body["work_item"]
    assert item["assignee_type"] == "assistant" and item["assignee_name"] == "Sales assistant" and item["source"] == "inbound"
    arabic = client.post("/api/v1/assistants/route", headers=MEMBER, json={"channel": "email", "sender": "a@example.com", "body": "كم سعر الإطار؟"}).json()
    assert arabic["assistant"]["id"] == sales["id"]
    explicit = client.post("/api/v1/assistants/route", headers=MEMBER, json={
        "channel": "email", "sender": "b@example.com", "subject": "Price", "body": "hello", "assistant_id": support["id"]}).json()
    assert explicit["assistant"]["id"] == support["id"]
    nothing = client.post("/api/v1/assistants/route", headers=MEMBER, json={"channel": "web", "sender": "c", "body": "Hello there"}).json()
    assert nothing["assistant"] is None and nothing["work_item"]["assignee_type"] is None
    feed = [a for a in client.get("/api/v1/team/activity", headers=MEMBER).json()["items"] if a["action"] == "assistant.route"]
    assert feed[-1]["actor_type"] == "assistant" and feed[-1]["actor_name"] == "Sales assistant"
    # An assistant with open work cannot be deleted; it can be turned off.
    refused = client.delete(f"/api/v1/assistants/{sales['id']}", headers=OWNER)
    assert refused.status_code == 409 and "turn the assistant off" in refused.json()["error"]["message"]
    assert client.delete(f"/api/v1/assistants/{support['id']}", headers=MEMBER).status_code == 403


def _work_items(client: TestClient) -> list[dict]:
    return client.get("/api/v1/work-items", headers=OWNER, params={"limit": 100}).json()["items"]


def test_arriving_messages_route_only_to_assistants_that_opted_into_the_channel(client: TestClient) -> None:
    from anum_api.assistants import ArrivedMessage, auto_route_inbound

    sales = new_assistant(client)
    assert sales["inbound_channels"] == []
    price = ArrivedMessage(sender="+971500000000", body="What is your price for 4 tyres?", external_ref="whatsapp:wacv_1")
    # Nobody opted in: nothing reaches the board.
    assert auto_route_inbound(TENANT, WORKSPACE, "whatsapp", [price]) == 0
    assert _work_items(client) == []
    updated = client.put(f"/api/v1/assistants/{sales['id']}", headers=OWNER, json={
        "department": "sales", "name": "Sales assistant", "allowed_tools": ["anum.respond"], "routing_keywords": ["price"],
        "inbound_channels": ["whatsapp", "whatsapp"]})
    assert updated.status_code == 200 and updated.json()["inbound_channels"] == ["whatsapp"]
    # A client that does not send the field keeps the stored channels.
    kept = client.put(f"/api/v1/assistants/{sales['id']}", headers=OWNER, json={
        "department": "sales", "name": "Sales assistant", "allowed_tools": ["anum.respond"], "routing_keywords": ["price"]})
    assert kept.json()["inbound_channels"] == ["whatsapp"]
    bad = client.put(f"/api/v1/assistants/{sales['id']}", headers=OWNER, json={
        "department": "sales", "name": "Sales assistant", "inbound_channels": ["sms"]})
    assert bad.status_code == 422
    hello = ArrivedMessage(sender="+971500000001", body="Hello", external_ref="whatsapp:wacv_2")
    assert auto_route_inbound(TENANT, WORKSPACE, "whatsapp", [price, hello]) == 1
    # Email is not on the assistant's list; the same conversation is not filed twice while open.
    assert auto_route_inbound(TENANT, WORKSPACE, "email", [ArrivedMessage(sender="a@example.com", body="price?")]) == 0
    assert auto_route_inbound(TENANT, WORKSPACE, "whatsapp", [price]) == 0
    items = _work_items(client)
    assert len(items) == 1 and items[0]["assignee_id"] == sales["id"] and items[0]["source"] == "inbound"
    detail = client.get(f"/api/v1/work-items/{items[0]['id']}", headers=OWNER).json()
    assert detail["source_detail"]["automatic"] is True and detail["source_detail"]["external_ref"] == "whatsapp:wacv_1"
    feed = [a for a in client.get("/api/v1/team/activity", headers=MEMBER).json()["items"] if a["action"] == "assistant.route"]
    assert feed[-1]["actor_name"] == "Sales assistant"
    # A turned-off assistant receives nothing.
    client.put(f"/api/v1/assistants/{sales['id']}", headers=OWNER, json={
        "department": "sales", "name": "Sales assistant", "allowed_tools": ["anum.respond"], "routing_keywords": ["price"], "enabled": False})
    assert auto_route_inbound(TENANT, WORKSPACE, "whatsapp", [ArrivedMessage(sender="+1", body="price", external_ref="whatsapp:x")]) == 0


def test_auto_routing_never_raises_into_ingestion(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import assistants

    def broken(*_args, **_kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(assistants, "open_team_unit", broken)
    assert assistants.auto_route_inbound(TENANT, WORKSPACE, "email", [assistants.ArrivedMessage(sender="a", body="price")]) == 0


def test_whatsapp_webhook_and_email_sync_hand_new_messages_to_routing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from anum_api import assistants, email_inbox, whatsapp
    from anum_api.email_providers import EmailProvider

    captured: list[tuple[str, list]] = []
    monkeypatch.setattr(assistants, "auto_route_inbound", lambda _t, _w, channel, messages: captured.append((channel, messages)) or 0)
    context = whatsapp.system_context(TENANT, WORKSPACE)
    whatsapp._route_to_assistants(context, [("Sara (+971500000000)", "price please", "wacv_9")])
    assert captured[-1][0] == "whatsapp" and captured[-1][1][0].external_ref == "whatsapp:wacv_9"

    now = utc_now()
    account = email_inbox.EmailAccount(id="acct_1", tenant_id=TENANT, workspace_id=WORKSPACE, provider=EmailProvider.GMAIL,
                                       address="shop@example.com", connected_by="owner_1", last_sync_at=now - timedelta(minutes=5),
                                       created_at=now, updated_at=now)

    def mail(pid: str, **fields) -> email_inbox.EmailMessage:
        base = {"id": f"eml_{pid}", "tenant_id": TENANT, "workspace_id": WORKSPACE, "account_id": "acct_1", "provider_message_id": pid,
                "thread_id": f"t_{pid}", "subject": "Quote", "from_address": "buyer@example.com", "snippet": "price for 4 tyres",
                "received_at": now, "created_at": now, "updated_at": now}
        return email_inbox.EmailMessage(**{**base, **fields})

    class Known:
        def get_by_provider_id(self, _account_id: str, pid: str):
            return object() if pid == "seen" else None

    incoming = [mail("new"), mail("seen"), mail("old", received_at=now - timedelta(days=3)), mail("bulk", bulk=True),
                mail("mine", from_address="Shop@example.com"), mail("sent", labels=["SENT"])]
    arrived = email_inbox._arrived_for_routing(Known(), account, incoming)
    assert [a.external_ref for a in arrived] == ["email:acct_1:t_new"] and arrived[0].body == "price for 4 tyres"
    # Private mailboxes and the first (history) sync never feed the shared board.
    assert email_inbox._arrived_for_routing(Known(), account.model_copy(update={"visibility": email_inbox.Visibility.PRIVATE}), incoming) == []
    assert email_inbox._arrived_for_routing(Known(), account.model_copy(update={"last_sync_at": None}), incoming) == []


def test_hand_off_creates_an_agent_task_that_runs_as_the_assistant(client: TestClient) -> None:
    sales = new_assistant(client, autonomy="suggest", allowed_tools=["anum.respond"])
    item = client.post("/api/v1/work-items", headers=MEMBER, json={"title": "Draft a quote", "assignee": {"type": "assistant", "id": sales["id"]}}).json()
    handed = client.post(f"/api/v1/work-items/{item['id']}/hand-off", headers=MEMBER)
    assert handed.status_code == 200, handed.text
    task = handed.json()["task"]
    assert task["assistant_id"] == sales["id"] and handed.json()["work_item"]["status"] == "in_progress"
    assert handed.json()["work_item"]["agent_task_id"] == task["id"]
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=MEMBER).json()
    # anum.respond (a draft, no side effects) runs even under "suggest".
    assert run["task"]["status"] == "completed"
    feed = [a["summary"] for a in client.get("/api/v1/team/activity", headers=MEMBER).json()["items"]]
    assert 'Sales assistant started on "Draft a quote"' in feed
    person = client.post("/api/v1/work-items", headers=MEMBER, json={"title": "Mine", "assignee": {"type": "user", "id": "member_1"}}).json()
    assert client.post(f"/api/v1/work-items/{person['id']}/hand-off", headers=MEMBER).status_code == 422


def _evaluate(assistant: dict, tool: str) -> tuple[ToolPolicyOutcome, str]:
    from anum_api.main import tool_registry

    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_1", roles=["member"])
    now = utc_now()
    task = Task(id="task_x", title="t", prompt="p", status=TaskStatus.RUNNING, tenant_id=TENANT, workspace_id=WORKSPACE,
                created_at=now, updated_at=now, assistant_id=assistant["id"])
    runtime = AgentRuntime(None, memory_repository, tools=tool_registry)  # type: ignore[arg-type]
    decision = runtime.evaluate(ToolCall(name=tool, arguments={}), context, task)
    return decision.outcome, decision.reason


def test_assistant_profile_only_narrows_tool_policy(client: TestClient) -> None:
    suggest = new_assistant(client, allowed_tools=["anum.respond", "team.assign_task", "external.action"])
    assert _evaluate(suggest, "team.assign_task")[0] == ToolPolicyOutcome.REQUIRE_APPROVAL
    assert _evaluate(suggest, "anum.respond")[0] == ToolPolicyOutcome.ALLOW
    # High risk stays an approval; nothing is loosened.
    assert _evaluate(suggest, "external.action")[0] == ToolPolicyOutcome.REQUIRE_APPROVAL
    observe = new_assistant(client, name="Engineering", department="tyre_engineering", allowed_tools=["anum.respond", "team.assign_task"], autonomy="observe")
    outcome, reason = _evaluate(observe, "team.assign_task")
    assert outcome == ToolPolicyOutcome.BLOCK and "observe only" in reason
    acting = new_assistant(client, name="Ops", department="operations", allowed_tools=["team.assign_task"], autonomy="act_low_risk")
    assert _evaluate(acting, "team.assign_task")[0] == ToolPolicyOutcome.ALLOW
    outcome, reason = _evaluate(acting, "anum.respond")
    assert outcome == ToolPolicyOutcome.BLOCK and "not allowed to use anum.respond" in reason
    client.put(f"/api/v1/assistants/{acting['id']}", headers=OWNER, json={
        "department": "operations", "name": "Ops", "allowed_tools": ["team.assign_task"], "autonomy": "act_low_risk", "enabled": False})
    assert _evaluate(acting, "team.assign_task")[0] == ToolPolicyOutcome.BLOCK


# --------------------------------------------------------------------------- organisations


def onboard(client: TestClient, tenant_id: str = "acme_tyres", email: str = "admin@acme.example") -> dict:
    response = client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": tenant_id, "display_name": "Acme Tyres", "workspace_name": "Main", "first_admin_email": email, "plan": "trial"})
    assert response.status_code == 201, response.text
    return response.json()


def test_platform_operator_onboards_a_customer_in_one_step(client: TestClient) -> None:
    created = onboard(client)
    org = created["organisation"]
    assert org["tenant_id"] == "acme_tyres" and org["health"] == "setting_up"
    assert org["workspaces"] == 1 and org["active_members"] == 0 and org["pending_invitations"] == 1
    assert created["invitation_token"].startswith("anum_inv_") and created["workspace_id"] == "acme_tyres_main"
    # The operator is not a member of the customer organisation.
    assert ("acme_tyres", "acme_tyres_main", "owner_1") not in store.memberships
    # The first admin accepts with the email the invitation is bound to and becomes owner.
    admin = h("acme_admin", "member", tenant="acme_tyres", workspace="acme_tyres_main", **{"x-user-email": "admin@acme.example"})
    accepted = client.post("/api/v1/workspace-invitations/accept", headers=admin, json={"token": created["invitation_token"]})
    assert accepted.status_code == 200, accepted.text
    assert store.memberships[("acme_tyres", "acme_tyres_main", "acme_admin")].role == "owner"
    listed = client.get("/api/v1/platform/organizations", headers=OPERATOR).json()
    assert listed[0]["active_members"] == 1 and listed[0]["health"] in {"idle", "active"}
    # Counts only: the summary carries no names, titles or emails of the customer's data.
    assert set(listed[0]) == {
        "tenant_id", "display_name", "plan", "first_workspace_id", "created_at", "workspaces", "active_members",
        "pending_invitations", "open_agent_tasks", "open_work_items", "overdue_work_items", "last_activity_at", "health"}
    assert any(r.action == "platform.organization.create" and r.tenant_id == TENANT for r in store.audit_records)
    assert any(r.action == "platform.organization.create" and r.tenant_id == "acme_tyres" for r in store.audit_records)
    reinvite = client.post("/api/v1/platform/organizations/acme_tyres/invitation", headers=OPERATOR)
    assert reinvite.status_code == 409


def test_only_platform_operators_use_the_console_and_ids_are_unique(client: TestClient) -> None:
    denied = client.get("/api/v1/platform/organizations", headers=OWNER)
    assert denied.status_code == 403 and "platform_operator" in denied.json()["error"]["message"]
    onboard(client)
    again = client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": "acme_tyres", "display_name": "Other", "first_admin_email": "x@example.com"})
    assert again.status_code == 409
    taken = client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": TENANT, "display_name": "Mine", "first_admin_email": "x@example.com"})
    assert taken.status_code == 409
    bad = client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": "Bad Id", "display_name": "x", "first_admin_email": "nope"})
    assert bad.status_code == 422
    # Another operator does not see or change this operator's organisations.
    other = h("operator_2", "owner,platform_operator")
    assert client.get("/api/v1/platform/organizations", headers=other).json() == []
    assert client.patch("/api/v1/platform/organizations/acme_tyres", headers=other, json={"plan": "standard"}).status_code == 404


def test_operator_changes_the_plan_and_reissues_a_lapsed_invitation(client: TestClient) -> None:
    created = onboard(client)
    changed = client.patch("/api/v1/platform/organizations/acme_tyres", headers=OPERATOR, json={"plan": "standard"})
    assert changed.status_code == 200 and changed.json()["plan"] == "standard"
    reissued = client.post("/api/v1/platform/organizations/acme_tyres/invitation", headers=OPERATOR)
    assert reissued.status_code == 201
    assert reissued.json()["invitation_token"] != created["invitation_token"]
    old = store.invitations[created["invitation_id"]]
    assert old.status == "revoked"


def test_branding_is_read_by_members_and_changed_by_the_org_admin(client: TestClient) -> None:
    assert client.get("/api/v1/organization/branding", headers=MEMBER).json()["display_name"] == "Platform Co"
    denied = client.put("/api/v1/organization/branding", headers=OWNER, json={"display_name": "New"})
    assert denied.status_code == 403 and "org_admin" in denied.json()["error"]["message"]
    admin = h("owner_1", "owner,org_admin")
    assert client.put("/api/v1/organization/branding", headers=admin, json={"display_name": "  Tyre  House "}).json()["display_name"] == "Tyre House"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    logo = client.put("/api/v1/organization/branding/logo", headers={**admin, "content-type": "image/png"}, content=png)
    assert logo.status_code == 200 and logo.json()["has_logo"] is True
    served = client.get("/api/v1/organization/branding/logo", headers=MEMBER)
    assert served.status_code == 200 and served.content == png and served.headers["x-content-type-options"] == "nosniff"
    fake = client.put("/api/v1/organization/branding/logo", headers={**admin, "content-type": "image/png"}, content=b"<svg/>")
    assert fake.status_code == 415
    huge = client.put("/api/v1/organization/branding/logo", headers={**admin, "content-type": "image/png"}, content=b"\x89PNG\r\n\x1a\n" + b"0" * 300_000)
    assert huge.status_code == 413
    assert client.delete("/api/v1/organization/branding/logo", headers=admin).json()["has_logo"] is False
    assert client.get("/api/v1/organization/branding/logo", headers=MEMBER).status_code == 404


def test_my_organisations_and_local_tenant_switch(client: TestClient) -> None:
    created = onboard(client)
    admin = h("owner_1", "member", tenant="acme_tyres", workspace="acme_tyres_main", **{"x-user-email": "admin@acme.example"})
    assert client.post("/api/v1/workspace-invitations/accept", headers=admin, json={"token": created["invitation_token"]}).status_code == 200
    orgs = client.get("/api/v1/me/organizations", headers=OWNER).json()
    assert [o["tenant_id"] for o in orgs] == [TENANT, "acme_tyres"]
    assert orgs[0]["current"] is True and orgs[1]["display_name"] == "Acme Tyres"
    assert orgs[1]["workspaces"] == [{"workspace_id": "acme_tyres_main", "workspace_name": "Main", "role": "owner"}]
    assert client.get("/api/v1/me/organizations", headers=MEMBER).json()[0]["tenant_id"] == TENANT
    session = client.post("/api/v1/auth/local/session", json={"tenant_id": TENANT, "workspace_id": WORKSPACE, "user_id": "owner_1"}).json()
    token = {"authorization": f"Bearer {session['access_token']}"}
    switched = client.post("/api/v1/auth/local/tenant/switch", headers=token, json={"tenant_id": "acme_tyres", "workspace_id": "acme_tyres_main"})
    assert switched.status_code == 200, switched.text
    assert switched.json()["context"]["tenant_id"] == "acme_tyres" and switched.json()["context"]["roles"] == ["owner"]
    new_token = {"authorization": f"Bearer {switched.json()['access_token']}"}
    refused = client.post("/api/v1/auth/local/tenant/switch", headers=new_token, json={"tenant_id": "tenant_nowhere", "workspace_id": "workspace_nowhere"})
    assert refused.status_code == 403


# --------------------------------------------------------------------------- OIDC: several organisations


@pytest.fixture
def oidc(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, SigningKey]]:
    key = SigningKey("kid-team")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "repository_backend", "memory")
    dependencies.set_oidc_validator(make_validator(FakeJwksEndpoint(key)))
    _clear()
    try:
        yield TestClient(app), key
    finally:
        dependencies.set_oidc_validator(None)
        _clear()


def _membership(tenant: str, workspace: str, user: str, role: str) -> None:
    now = utc_now()
    store.memberships[(tenant, workspace, user)] = WorkspaceMembership(
        tenant_id=tenant, workspace_id=workspace, user_id=user, role=role, created_at=now, updated_at=now)


def test_oidc_token_switches_only_to_listed_organisations_and_drops_org_roles(oidc) -> None:
    from test_identity import SUBJECT, TENANT as HOME, WORKSPACE as HOME_WS

    client, key = oidc
    _membership(HOME, HOME_WS, SUBJECT, "owner")
    _membership("tenant_customer", "workspace_customer", SUBJECT, "member")
    token = key.sign(tenant_ids=["tenant_customer"], realm_access={"roles": ["owner", "org_admin", "platform_operator"]})
    home = client.get("/api/v1/workspace-memberships/current", headers=bearer(token))
    assert home.status_code == 200 and home.json()["tenant_id"] == HOME
    other = client.get("/api/v1/workspace-memberships/current",
                       headers=bearer(token, **{"x-tenant-id": "tenant_customer", "x-workspace-id": "workspace_customer"}))
    assert other.status_code == 200 and other.json()["role"] == "member"
    # Organisation roles belong to the home tenant only.
    console = client.get("/api/v1/platform/organizations",
                         headers=bearer(token, **{"x-tenant-id": "tenant_customer", "x-workspace-id": "workspace_customer"}))
    assert console.status_code == 403
    assert client.get("/api/v1/platform/organizations", headers=bearer(token)).status_code == 200
    unlisted = client.get("/api/v1/workspace-memberships/current",
                          headers=bearer(key.sign(), **{"x-tenant-id": "tenant_customer", "x-workspace-id": "workspace_customer"}))
    assert unlisted.status_code == 403
    orgs = client.get("/api/v1/me/organizations", headers=bearer(token)).json()
    assert [o["tenant_id"] for o in orgs] == [HOME, "tenant_customer"]
    # Without tenant_ids in the token the other organisation is not offered.
    assert [o["tenant_id"] for o in client.get("/api/v1/me/organizations", headers=bearer(key.sign())).json()] == [HOME]
