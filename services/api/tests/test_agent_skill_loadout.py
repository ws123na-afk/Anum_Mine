"""Agents load their skills (docs/agents.md#skills), memory backend: the loadout an agent's page
shows, which agents use a skill, and runs and chats as an agent really loading its installed
skills' instructions, with the caller's tenant and visibility enforced."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from skill_signing_support import signed

from anum_api.agent_policy import LOADED_SKILL_CHARS, AgentRunProfile, LoadedSkill
from anum_api.agent_profiles import agent_store, configure_runtime_catalog, load_agent
from anum_api.agent_skill_loadout import agent_run_profile
from anum_api.agent_tools import ToolResult, default_tool_registry
from anum_api.conversations import conversation_store
from anum_api.main import app, store, tool_registry
from anum_api.schemas import TenantContext
from anum_api.skills_api import skill_store

TENANT, WORKSPACE = "tenant_loadout", "workspace_loadout"


def headers(user: str = "user_owner", role: str = "member", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def ctx(user: str = "user_owner", role: str = "member") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=[role])


OWNER = headers()
OTHER = headers("user_other")
WS_OWNER = headers("user_boss", "owner")
ORG_ADMIN = headers("user_admin", "owner,org_admin")
VIEWER = headers("user_viewer", "viewer")


async def _search(*_: object) -> ToolResult:
    return ToolResult(status="succeeded", summary="searched")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    agent_store.clear()
    conversation_store.clear()
    skill_store.clear()
    store.audit_records.clear()
    configure_runtime_catalog(default_tool_registry(web_search_handler=_search))
    yield
    configure_runtime_catalog(tool_registry)
    agent_store.clear()
    skill_store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _publish(client: TestClient, skill_id: str, version: str, tools: list[str], instructions: str = "Write in the house style.", risk: str = "low") -> None:
    response = client.post(
        "/api/v1/skills/versions",
        headers=ORG_ADMIN,
        json=signed(client, ORG_ADMIN, {"skill_id": skill_id, "version": version, "name": skill_id.split(".")[-1].title(), "description": "A skill",
                                        "instructions": instructions, "required_tools": tools, "risk_level": risk}),
    )
    assert response.status_code == 201, response.text


def _install(client: TestClient, skill_id: str, version: str, tools: list[str]) -> None:
    response = client.post(
        "/api/v1/skills/installations", headers=WS_OWNER, json={"skill_id": skill_id, "version": version, "approved_tools": tools}
    )
    assert response.status_code == 201, response.text


def _agent(client: TestClient, hdrs: dict[str, str] = OWNER, **fields) -> dict:  # type: ignore[no-untyped-def]
    body = {"name": "Writer", "category": "work"} | fields
    response = client.post("/api/v1/agents", headers=hdrs, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _loadout(client: TestClient, agent_id: str, hdrs: dict[str, str] = OWNER) -> dict:
    response = client.get(f"/api/v1/agents/{agent_id}/skills", headers=hdrs)
    assert response.status_code == 200, response.text
    return response.json()


def test_loadout_shows_each_skill_state_version_tools_and_how_it_loads(client: TestClient) -> None:
    _publish(client, "acme.release-notes", "1.0.0", ["anum.respond"], "Release notes: one line per change.")
    _install(client, "acme.release-notes", "1.0.0", ["anum.respond"])
    agent = _agent(
        client, allowed_skills=["acme.release-notes", "anum.document-drafting", "anum.web-search"], allowed_tools=["web_search"]
    )
    loadout = _loadout(client, agent["id"])
    by_id = {item["id"]: item for item in loadout["items"]}
    assert loadout["agent_version"] == 1 and loadout["can_edit"] is True
    assert loadout["ready"] == 3 and loadout["attention"] == 0

    installed = by_id["acme.release-notes"]
    assert installed["source"] == "installed" and installed["version"] == "1.0.0" and installed["state"] == "ready"
    assert installed["activation"] == "always" and installed["loads_instructions"] is True
    assert installed["tools"] == [{"name": "anum.respond", "registered": True, "allowed": True, "granted": True}]

    search = by_id["anum.web-search"]
    assert search["source"] == "builtin" and search["version"] == "1.0.0" and search["activation"] == "on_request"
    assert "search" in search["triggers"] and search["loads_instructions"] is False
    assert search["tools"] == [{"name": "web_search", "registered": True, "allowed": True, "granted": None}]

    # A newer published version is offered as an upgrade; the agent keeps the installed one.
    _publish(client, "acme.release-notes", "1.1.0", ["anum.respond"])
    assert {i["id"]: i for i in _loadout(client, agent["id"])["items"]}["acme.release-notes"]["upgrade_available"] is True


def test_states_say_why_a_skill_does_not_load_and_who_can_fix_it(client: TestClient) -> None:
    _publish(client, "acme.crm", "1.0.0", ["anum.respond", "external.action"])
    _install(client, "acme.crm", "1.0.0", ["anum.respond"])  # external.action not granted
    _publish(client, "acme.notes", "1.0.0", ["anum.respond"])
    _install(client, "acme.notes", "1.0.0", ["anum.respond"])
    _publish(client, "acme.later", "1.0.0", ["anum.respond"])
    _install(client, "acme.later", "1.0.0", ["anum.respond"])
    agent = _agent(client, allowed_skills=["acme.crm", "acme.notes", "acme.later", "anum.web-search"], allowed_tools=["web_search"])

    client.patch("/api/v1/skills/installations/acme.notes", headers=WS_OWNER, json={"enabled": False})
    assert client.delete("/api/v1/skills/installations/acme.later", headers=WS_OWNER).status_code == 204
    # The deployment loses the web search tool after the agent was saved.
    configure_runtime_catalog(default_tool_registry())

    by_id = {item["id"]: item for item in _loadout(client, agent["id"])["items"]}
    assert by_id["acme.crm"]["state"] == "needs_tools" and "did not grant external.action" in by_id["acme.crm"]["reason"]
    assert by_id["acme.notes"]["state"] == "disabled" and "workspace owner" in by_id["acme.notes"]["reason"]
    assert by_id["acme.later"]["state"] == "not_installed" and "installs it in Skills" in by_id["acme.later"]["reason"]
    assert by_id["anum.web-search"]["state"] == "unavailable"
    assert all(item["loads_instructions"] is False for item in by_id.values())

    profile = load_agent(ctx(), agent["id"])
    assert profile is not None
    assert agent_run_profile(ctx(), profile).loaded_skills == []


def test_a_task_as_the_agent_loads_its_installed_skills(client: TestClient) -> None:
    _publish(client, "acme.release-notes", "2.0.0", ["anum.respond"], "Release notes: one line per change.")
    _install(client, "acme.release-notes", "2.0.0", ["anum.respond"])
    agent = _agent(client, allowed_skills=["acme.release-notes"])

    profile = load_agent(ctx(), agent["id"])
    assert profile is not None
    snapshot = agent_run_profile(ctx(), profile)
    assert [(s.id, s.version) for s in snapshot.loaded_skills] == [("acme.release-notes", "2.0.0")]
    assert snapshot.loaded_skills[0].granted_tools == ["anum.respond"]
    persona = snapshot.persona()
    assert "Skill loaded for this agent: Release-Notes 2.0.0 (acme.release-notes)" in persona
    assert "Release notes: one line per change." in persona
    assert persona.endswith("which ANUM decides outside the model.")

    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Notes", "prompt": "Summarize the release", "agent_id": agent["id"]}).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER).json()
    assert run["run"]["checkpoint"]["agent"]["loaded_skills"][0]["id"] == "acme.release-notes"
    model_step = next(step for step in run["run"]["steps"] if step["type"] == "model_call")
    assert model_step["metadata"]["loaded_skills"] == [{"id": "acme.release-notes", "version": "2.0.0"}]
    # Ids and versions only in the step; the instruction text is not repeated there.
    assert "one line per change" not in repr(model_step["metadata"])

    # Disabled in the workspace: the next run does not load it.
    client.patch("/api/v1/skills/installations/acme.release-notes", headers=WS_OWNER, json={"enabled": False})
    again = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Notes 2", "prompt": "Summarize", "agent_id": agent["id"]}).json()
    run2 = client.post(f"/api/v1/tasks/{again['id']}/run", headers=OWNER).json()
    assert run2["run"]["checkpoint"]["agent"]["loaded_skills"] == []


def test_loaded_instructions_are_capped_and_never_grant_tools() -> None:
    long = "x" * (LOADED_SKILL_CHARS + 500)
    snapshot = AgentRunProfile(
        id="agent_x", name="X", loaded_skills=[LoadedSkill(id="a.b", version="1.0.0", name="B", instructions=long, granted_tools=["web_search"])]
    )
    assert snapshot.persona().count("x" * LOADED_SKILL_CHARS) == 1 and "x" * (LOADED_SKILL_CHARS + 1) not in snapshot.persona()
    # A loaded skill's granted tool is not an agent tool: the allowlist is the agent's own.
    assert snapshot.tool_allowed("web_search") is False


def test_old_checkpoints_without_loaded_skills_still_parse() -> None:
    restored = AgentRunProfile.model_validate({"id": "agent_x", "name": "X", "allowed_skills": ["anum.document-drafting"]})
    assert restored.loaded_skills == []


def test_chat_with_the_agent_uses_the_loaded_skill(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _publish(client, "acme.tone", "1.0.0", ["anum.respond"], "Always greet the customer by name.")
    _install(client, "acme.tone", "1.0.0", ["anum.respond"])
    agent = _agent(client, allowed_skills=["acme.tone"])
    seen: dict[str, str | None] = {}
    from anum_api import conversations

    real = conversations.build_messages

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen["persona"] = kwargs.get("persona")
        return real(*args, **kwargs)

    monkeypatch.setattr(conversations, "build_messages", spy)
    thread = client.post("/api/v1/conversations", headers=OWNER, json={"agent_id": agent["id"]}).json()
    reply = client.post(f"/api/v1/conversations/{thread['id']}/ask", headers=OWNER, json={"content": "Hello"})
    assert reply.status_code == 200
    assert "Always greet the customer by name." in str(seen["persona"])


def test_loadout_and_usage_follow_visibility_and_tenancy(client: TestClient) -> None:
    _publish(client, "acme.notes", "1.0.0", ["anum.respond"])
    _install(client, "acme.notes", "1.0.0", ["anum.respond"])
    private = _agent(client, name="Mine", allowed_skills=["acme.notes", "anum.document-drafting"])
    shared = _agent(client, name="Team", sharing="workspace", allowed_skills=["acme.notes"])
    _agent(client, OTHER, name="Theirs", allowed_skills=["anum.document-drafting"])

    # Another member cannot see a private agent's loadout (the same 404 as a missing agent).
    assert client.get(f"/api/v1/agents/{private['id']}/skills", headers=OTHER).status_code == 404
    assert client.get("/api/v1/agents/agent_missing/skills", headers=OWNER).status_code == 404
    seen = _loadout(client, shared["id"], OTHER)
    assert seen["can_edit"] is False and seen["items"][0]["id"] == "acme.notes"
    assert _loadout(client, shared["id"], VIEWER)["can_edit"] is False

    usage = {u["skill_id"]: u for u in client.get("/api/v1/agents/skills/usage", headers=OWNER).json()["items"]}
    assert [a["name"] for a in usage["acme.notes"]["agents"]] == ["Mine", "Team"]
    assert usage["anum.document-drafting"]["count"] == 1  # "Theirs" is private to user_other
    others = {u["skill_id"]: u for u in client.get("/api/v1/agents/skills/usage", headers=OTHER).json()["items"]}
    assert others["acme.notes"]["count"] == 1 and others["anum.document-drafting"]["agents"][0]["name"] == "Theirs"

    # Another workspace sees none of it.
    elsewhere = client.get("/api/v1/agents/skills/usage", headers=headers(workspace="workspace_elsewhere")).json()
    assert elsewhere["items"] == []
    assert client.get(f"/api/v1/agents/{shared['id']}/skills", headers=headers(workspace="workspace_elsewhere")).status_code == 404


def test_usage_and_loadout_need_task_read(client: TestClient) -> None:
    agent = _agent(client)
    no_role = headers("user_guest", "guest")
    assert client.get("/api/v1/agents/skills/usage", headers=no_role).status_code == 403
    assert client.get(f"/api/v1/agents/{agent['id']}/skills", headers=no_role).status_code == 403


def test_a_run_fails_closed_when_the_skill_store_cannot_be_read(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent never runs without the skills it was configured with while the caller thinks they load."""
    _publish(client, "acme.notes", "1.0.0", ["anum.respond"])
    _install(client, "acme.notes", "1.0.0", ["anum.respond"])
    agent = _agent(client, allowed_skills=["acme.notes"])
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "t", "prompt": "Summarize", "agent_id": agent["id"]}).json()
    from anum_api import skills_api

    def unreadable(_context):  # type: ignore[no-untyped-def]
        raise RuntimeError("skill store unreachable")

    monkeypatch.setattr(skills_api, "open_skill_store", unreadable)
    refused = TestClient(app, raise_server_exceptions=False).post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER)
    assert refused.status_code >= 500
    monkeypatch.undo()
    after = client.get(f"/api/v1/tasks/{task['id']}", headers=OWNER).json()
    assert after["status"] != "completed"
