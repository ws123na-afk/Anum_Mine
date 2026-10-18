"""User-built agents (docs/agents.md), memory backend: profiles, permissions, templates,
memories, runs "as" an agent and agent chat threads."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from anum_api import agent_profiles
from anum_api.agent_policy import AgentRunProfile, apply_agent_policy
from anum_api.agent_profiles import (
    AgentAutonomy,
    AutonomyLevel,
    agent_store,
    configure_runtime_catalog,
    resolve_template,
    TEMPLATES,
)
from anum_api.agent_tools import (
    ToolCall,
    ToolDefinition,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResult,
    default_tool_registry,
)
from anum_api.conversations import build_messages, conversation_store
from anum_api.dependencies import memory_note_repository
from anum_api.main import app, store, tool_registry
from anum_api.retrieval import (
    ChunkInput,
    IndexStatus,
    InMemoryRetrievalStore,
    LocalEmbedder,
    RetrievalSource,
    Retriever,
    SourceType,
    memory_retrieval_store,
    sha256_text,
)
from anum_api.runtime import AgentRuntime
from anum_api.schemas import RiskLevel, TaskStatus, TenantContext, utc_now

TENANT, WORKSPACE = "tenant_agents", "workspace_agents"


def headers(user: str = "user_owner", role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


def ctx(user: str = "user_owner", role: str = "member") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=[role])


OWNER = headers()
OTHER = headers("user_other")
WS_OWNER = headers("user_boss", "owner")
VIEWER = headers("user_viewer", "viewer")


async def _lookup(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(status="succeeded", summary="looked up", output={"q": call.arguments})


def _registry_with_extra_tools():  # type: ignore[no-untyped-def]
    registry = default_tool_registry()
    registry.register(
        ToolDefinition(
            name="tyrepulse.plan_rotation", description="Plan a tyre rotation", risk_level=RiskLevel.LOW,
            category="general", action_kind="read",
        ),
        _lookup,
    )
    registry.register(
        ToolDefinition(
            name="fleet.admin_reset",
            description="Owner-only reset",
            risk_level=RiskLevel.MEDIUM,
            required_roles=frozenset({"owner"}),
            category="general",
            action_kind="update",
        ),
        _lookup,
    )
    return registry


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    agent_store.clear()
    conversation_store.clear()
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.audit_records.clear()
    memory_note_repository._notes.clear()
    memory_retrieval_store.clear()
    configure_runtime_catalog(_registry_with_extra_tools())
    yield
    configure_runtime_catalog(tool_registry)
    agent_store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _create(client: TestClient, hdrs: dict[str, str] = OWNER, **fields) -> dict:  # type: ignore[no-untyped-def]
    body = {"name": "Helper", "category": "work", "description": "Helps with things"} | fields
    response = client.post("/api/v1/agents", headers=hdrs, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _audit_actions() -> list[str]:
    return [r.action for r in store.audit_records if r.tenant_id == TENANT]


# --------------------------------------------------------------------------- CRUD


def test_create_read_update_delete_with_audit(client: TestClient) -> None:
    created = _create(
        client,
        name="Fleet",
        icon="truck",
        color="#0f766e",
        instructions="Plan tyre rotations.",
        allowed_tools=["tyrepulse.plan_rotation"],
        allowed_skills=["anum.document-drafting"],
    )
    assert created["owner_user_id"] == "user_owner" and created["status"] == "active"
    assert created["sharing"] == "private" and created["version"] == 1
    assert created["can_edit"] is True and created["can_run"] is True
    assert created["autonomy"] == {"read": "auto", "write": "ask", "external": "ask"}

    got = client.get(f"/api/v1/agents/{created['id']}", headers=OWNER).json()
    assert got["instructions"] == "Plan tyre rotations."

    updated = client.patch(
        f"/api/v1/agents/{created['id']}", headers=OWNER, json={"status": "paused", "name": "Fleet planner", "version": 1}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "paused" and updated.json()["version"] == 2
    assert updated.json()["can_run"] is False

    stale = client.patch(f"/api/v1/agents/{created['id']}", headers=OWNER, json={"name": "x", "version": 1})
    assert stale.status_code == 409

    assert client.delete(f"/api/v1/agents/{created['id']}", headers=OWNER).status_code == 204
    assert client.get(f"/api/v1/agents/{created['id']}", headers=OWNER).status_code == 404
    assert _audit_actions() == ["agent.created", "agent.updated", "agent.deleted"]
    updated_record = next(r for r in store.audit_records if r.action == "agent.updated")
    assert list(updated_record.metadata["changed"]) == ["name", "status"]
    # Configuration only, never the instruction text.
    assert "Plan tyre rotations" not in repr([r.metadata for r in store.audit_records])


def test_private_agents_are_invisible_and_shared_ones_follow_edit_rules(client: TestClient) -> None:
    private = _create(client, name="Mine")
    shared = _create(client, name="Team", sharing="workspace")

    others = client.get("/api/v1/agents", headers=OTHER).json()
    assert [a["name"] for a in others["items"]] == ["Team"]
    assert client.get(f"/api/v1/agents/{private['id']}", headers=OTHER).status_code == 404
    assert client.patch(f"/api/v1/agents/{private['id']}", headers=OTHER, json={"name": "x"}).status_code == 404

    seen = client.get(f"/api/v1/agents/{shared['id']}", headers=OTHER).json()
    assert seen["can_edit"] is False and seen["can_run"] is True
    refused = client.patch(f"/api/v1/agents/{shared['id']}", headers=OTHER, json={"name": "x"})
    assert refused.status_code == 403 and "creator" in refused.json()["error"]["message"]
    assert client.delete(f"/api/v1/agents/{shared['id']}", headers=OTHER).status_code == 403

    # A workspace owner may edit a shared agent but not change who it is shared with.
    assert client.patch(f"/api/v1/agents/{shared['id']}", headers=WS_OWNER, json={"name": "Team 2"}).status_code == 200
    unshare = client.patch(f"/api/v1/agents/{shared['id']}", headers=WS_OWNER, json={"sharing": "private"})
    assert unshare.status_code == 403
    assert client.get(f"/api/v1/agents/{private['id']}", headers=WS_OWNER).status_code == 404


def test_viewers_read_shared_agents_but_cannot_create_or_run(client: TestClient) -> None:
    shared = _create(client, sharing="workspace")
    assert client.post("/api/v1/agents", headers=VIEWER, json={"name": "x"}).status_code == 403
    seen = client.get(f"/api/v1/agents/{shared['id']}", headers=VIEWER).json()
    assert seen["can_edit"] is False and seen["can_run"] is False


def test_list_filters_search_counts_and_pages(client: TestClient) -> None:
    for index in range(5):
        _create(client, name=f"Work {index}", category="work")
    _create(client, name="Groceries", category="personal", description="Weekly shopping list")
    _create(client, name="Odd one", category="custom", department="Logistics")

    everything = client.get("/api/v1/agents", headers=OWNER).json()
    assert everything["counts"] == {"all": 7, "work": 5, "personal": 1, "custom": 1}
    work = client.get("/api/v1/agents?category=work&limit=2", headers=OWNER).json()
    assert len(work["items"]) == 2 and work["next_cursor"]
    second = client.get(f"/api/v1/agents?category=work&limit=2&cursor={work['next_cursor']}", headers=OWNER).json()
    assert {a["id"] for a in second["items"]}.isdisjoint({a["id"] for a in work["items"]})
    assert [a["name"] for a in client.get("/api/v1/agents?q=shopping", headers=OWNER).json()["items"]] == ["Groceries"]
    assert [a["name"] for a in client.get("/api/v1/agents?q=logistics", headers=OWNER).json()["items"]] == ["Odd one"]
    assert client.get("/api/v1/agents?cursor=agent_nope", headers=OWNER).status_code == 400


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"allowed_tools": ["gmail.send"]}, "does not exist"),
        ({"allowed_tools": ["fleet.admin_reset"]}, "owner role"),
        ({"allowed_skills": ["anum.nope"]}, "install and enable"),
        ({"allowed_skills": ["anum.external-action"]}, "needs the tool external.action"),
        ({"knowledge_mode": "selected"}, "Pick at least one"),
        ({"knowledge_mode": "selected", "knowledge_sources": [{"type": "file", "id": "file_missing"}]}, "not indexed"),
        ({"autonomy": {"read": "auto", "write": "auto", "external": "auto"}}, "always need approval"),
        ({"icon": "rocket"}, "Unknown icon"),
        ({"color": "red"}, "pattern"),
    ],
)
def test_capabilities_are_never_wider_than_allowed(client: TestClient, fields: dict, message: str) -> None:
    response = client.post("/api/v1/agents", headers=OWNER, json={"name": "x"} | fields)
    assert response.status_code == 422, response.text
    assert message in response.text


def test_owner_role_tool_is_allowed_for_owners_and_shown_unavailable_to_members(client: TestClient) -> None:
    owned = client.post("/api/v1/agents", headers=WS_OWNER, json={"name": "Reset", "allowed_tools": ["fleet.admin_reset"]})
    assert owned.status_code == 201
    catalog = client.get("/api/v1/agents/catalog", headers=OWNER).json()
    reset = next(tool for tool in catalog["tools"] if tool["name"] == "fleet.admin_reset")
    assert reset["available_to_you"] is False and "owner" in reset["reason"]
    respond = next(tool for tool in catalog["tools"] if tool["name"] == "anum.respond")
    assert respond["always_on"] is True and respond["category"] == "read"


def test_templates_enable_only_tools_that_exist(client: TestClient) -> None:
    catalog = client.get("/api/v1/agents/catalog", headers=OWNER).json()
    templates = {t["id"]: t for t in catalog["templates"]}
    assert set(templates) == {"fleet", "research", "email", "calendar", "document", "call"}
    assert templates["fleet"]["allowed_tools"] == ["tyrepulse.plan_rotation"]
    assert templates["fleet"]["missing_capabilities"] == []
    # No SearXNG in this deployment: research has no web tool and says what is missing.
    assert templates["research"]["allowed_tools"] == []
    assert templates["research"]["allowed_skills"] == ["anum.document-drafting"]
    assert templates["research"]["missing_capabilities"]
    assert templates["email"]["allowed_tools"] == [] and templates["email"]["missing_capabilities"]
    assert templates["call"]["allowed_tools"] == [] and "voice provider" in templates["call"]["missing_capabilities"][0]
    # Every template, as offered, can be saved as an agent.
    for template in templates.values():
        body = {key: template[key] for key in (
            "name", "icon", "color", "category", "description", "instructions", "model_route",
            "allowed_tools", "allowed_skills", "autonomy", "knowledge_mode",
        )} | {"template_id": template["id"]}
        assert client.post("/api/v1/agents", headers=OWNER, json=body).status_code == 201, template["id"]


def test_template_with_web_search_present() -> None:
    research = next(spec for spec in TEMPLATES if spec.id == "research")
    resolved = resolve_template(research, {"anum.respond", "web_search"}, {"anum.web-search", "anum.document-drafting"})
    assert resolved.allowed_tools == ["web_search"] and resolved.missing_capabilities == []
    assert resolved.allowed_skills == ["anum.web-search", "anum.document-drafting"]


def test_floor_follows_the_workspace_medium_risk_policy(client: TestClient) -> None:
    assert client.get("/api/v1/agents/catalog", headers=OWNER).json()["autonomy_floor"]["write"] == "auto"
    client.put(
        "/api/v1/approval-policy",
        headers=WS_OWNER,
        json={"two_person_rule": False, "medium_risk_requires_approval": True},
    )
    try:
        assert client.get("/api/v1/agents/catalog", headers=OWNER).json()["autonomy_floor"]["write"] == "ask"
    finally:
        client.put(
            "/api/v1/approval-policy",
            headers=WS_OWNER,
            json={"two_person_rule": False, "medium_risk_requires_approval": False},
        )


# --------------------------------------------------------------------------- memories


def test_agent_memories_are_scoped_paged_searchable_and_audited(client: TestClient) -> None:
    agent = _create(client, sharing="workspace")
    path = f"/api/v1/agents/{agent['id']}/memories"
    for index in range(3):
        assert client.post(path, headers=OWNER, json={"content": f"Fact number {index}"}).status_code == 201
    assert client.post(path, headers=OWNER, json={"content": "Truck 12 uses Michelin"}).status_code == 201

    page = client.get(f"{path}?limit=2", headers=OWNER).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["next_cursor"]
    assert [m["content"] for m in client.get(f"{path}?q=michelin", headers=OWNER).json()["items"]] == [
        "Truck 12 uses Michelin"
    ]
    # Others can read a shared agent's memories but not change them.
    assert client.get(path, headers=OTHER).json()["total"] == 4
    assert client.post(path, headers=OTHER, json={"content": "nope"}).status_code == 403
    memory_id = page["items"][0]["id"]
    assert client.delete(f"{path}/{memory_id}", headers=OTHER).status_code == 403
    assert client.delete(f"{path}/{memory_id}", headers=OWNER).status_code == 204
    assert client.delete(f"{path}/{memory_id}", headers=OWNER).status_code == 404
    assert "agent_memory.deleted" in _audit_actions()
    assert "Michelin" not in repr([r.metadata for r in store.audit_records])

    # Deleting the agent deletes its memories.
    client.delete(f"/api/v1/agents/{agent['id']}", headers=OWNER)
    assert agent_store.memories.get(agent["id"]) is None


def test_memory_from_a_task_must_be_one_of_the_agents_tasks(client: TestClient) -> None:
    agent = _create(client)
    other = client.post("/api/v1/tasks", headers=OWNER, json={"title": "t", "prompt": "plain task"}).json()
    response = client.post(f"/api/v1/agents/{agent['id']}/memories", headers=OWNER, json={"content": "x", "task_id": other["id"]})
    assert response.status_code == 422
    mine = client.post(
        "/api/v1/tasks", headers=OWNER, json={"title": "t", "prompt": "agent task", "agent_id": agent["id"]}
    ).json()
    saved = client.post(f"/api/v1/agents/{agent['id']}/memories", headers=OWNER, json={"content": "x", "task_id": mine["id"]})
    assert saved.status_code == 201 and saved.json()["source"] == "task"


# --------------------------------------------------------------------------- running as an agent


def test_run_a_task_as_an_agent_and_list_its_recent_tasks(client: TestClient) -> None:
    agent = _create(client, instructions="Always answer in one line.", model_route="fast")
    client.post(f"/api/v1/agents/{agent['id']}/memories", headers=OWNER, json={"content": "Depot is in Riyadh"})
    created = client.post(
        "/api/v1/tasks",
        headers=OWNER,
        json={"title": "Plan", "prompt": "Summarize the week", "agent_id": agent["id"]},
    )
    assert created.status_code == 201 and created.json()["agent_id"] == agent["id"]
    run = client.post(f"/api/v1/tasks/{created.json()['id']}/run", headers=OWNER).json()
    assert run["task"]["status"] == "completed"
    assert run["run"]["checkpoint"]["agent"]["id"] == agent["id"]
    model_step = next(step for step in run["run"]["steps"] if step["type"] == "model_call")
    assert model_step["metadata"]["agent_id"] == agent["id"]
    memory_step = next(step for step in run["run"]["steps"] if step["metadata"].get("source") == "agent_memory")
    assert len(memory_step["metadata"]["memory_ids"]) == 1
    # Knowledge "none": workspace retrieval is skipped and says why.
    assert any(step["metadata"].get("reason") == "agent_knowledge_none" for step in run["run"]["steps"])

    client.post("/api/v1/tasks", headers=OWNER, json={"title": "Plain", "prompt": "no agent"})
    recent = client.get(f"/api/v1/tasks?agent_id={agent['id']}", headers=OWNER).json()
    assert [task["title"] for task in recent] == ["Plan"]


def test_paused_deleted_or_hidden_agents_cannot_take_tasks(client: TestClient) -> None:
    agent = _create(client)
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "t", "prompt": "p", "agent_id": agent["id"]}).json()
    client.patch(f"/api/v1/agents/{agent['id']}", headers=OWNER, json={"status": "paused"})
    paused = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER)
    assert paused.status_code == 409 and "paused" in paused.json()["error"]["message"]
    assert client.post(
        "/api/v1/tasks", headers=OWNER, json={"title": "t", "prompt": "p", "agent_id": agent["id"]}
    ).status_code == 409
    # Someone else cannot use a private agent at all.
    assert client.post(
        "/api/v1/tasks", headers=OTHER, json={"title": "t", "prompt": "p", "agent_id": agent["id"]}
    ).status_code == 404
    client.delete(f"/api/v1/agents/{agent['id']}", headers=OWNER)
    gone = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER)
    assert gone.status_code == 404


def _runtime() -> AgentRuntime:
    from anum_api.dependencies import memory_repository
    from anum_api.model_gateway import build_default_model_gateway

    registry = _registry_with_extra_tools()
    return AgentRuntime(build_default_model_gateway(), memory_repository, tools=registry)


def _agent(**overrides) -> AgentRunProfile:  # type: ignore[no-untyped-def]
    return AgentRunProfile(id="agent_x", name="X", **overrides)


def test_agent_policy_only_makes_decisions_stricter() -> None:
    runtime = _runtime()
    context = ctx(role="owner")
    from anum_api.schemas import Task

    task = Task(
        id="task_x", title="t", prompt="p", status=TaskStatus.CREATED, tenant_id=TENANT,
        workspace_id=WORKSPACE, created_at=utc_now(), updated_at=utc_now(),
    )
    lookup = ToolCall(name="tyrepulse.plan_rotation")
    plain = runtime.evaluate(lookup, context)
    assert plain.outcome == ToolPolicyOutcome.ALLOW

    outside = runtime.new_run(task, agent=_agent())
    assert runtime.evaluate(lookup, context, run=outside).outcome == ToolPolicyOutcome.BLOCK
    assert runtime.evaluate(ToolCall(name="anum.respond"), context, run=outside).outcome == ToolPolicyOutcome.ALLOW

    asks = runtime.new_run(task, agent=_agent(allowed_tools=["tyrepulse.plan_rotation"], autonomy=AgentAutonomy(read="ask")))
    assert runtime.evaluate(lookup, context, run=asks).outcome == ToolPolicyOutcome.REQUIRE_APPROVAL

    off = runtime.new_run(task, agent=_agent(allowed_tools=["tyrepulse.plan_rotation"], autonomy=AgentAutonomy(read="off")))
    assert runtime.evaluate(lookup, context, run=off).outcome == ToolPolicyOutcome.BLOCK

    # auto never weakens: the high-risk external action still needs approval.
    external = runtime.new_run(task, agent=_agent(allowed_tools=["external.action"]))
    assert runtime.evaluate(ToolCall(name="external.action"), context, run=external).outcome == (
        ToolPolicyOutcome.REQUIRE_APPROVAL
    )
    # And a block from the platform stays a block whatever the agent says.
    blocked = ToolPolicyDecision(outcome=ToolPolicyOutcome.BLOCK, reason="no", risk_level=RiskLevel.BLOCKED)
    definition = runtime.tools.definition("tyrepulse.plan_rotation")
    assert apply_agent_policy(_agent(allowed_tools=["tyrepulse.plan_rotation"]), lookup, definition, blocked) is blocked


def test_external_auto_cannot_be_stored_on_a_snapshot() -> None:
    with pytest.raises(ValueError):
        AgentAutonomy(external=AutonomyLevel.AUTO)


def test_agent_planning_uses_only_its_skills_persona_and_route() -> None:
    from anum_api.agent_planning import AgentPlanner
    from anum_api.agent_skills import default_skill_registry
    from anum_api.model_gateway import ModelResponse, ModelRoute, ModelUsage
    from anum_api.schemas import Task

    seen: dict[str, object] = {}

    class Gateway:
        def for_route(self, route: ModelRoute):  # type: ignore[no-untyped-def]
            seen["route"] = route
            return self

        async def generate_text(self, prompt: str) -> ModelResponse:
            seen["prompt"] = prompt
            return ModelResponse(text="ok", usage=ModelUsage(input_tokens=1, output_tokens=1, provider="p", model="m"))

    registry = default_tool_registry(web_search_handler=_lookup)
    planner = AgentPlanner(Gateway(), default_skill_registry(), registry)  # type: ignore[arg-type]
    task = Task(
        id="t", title="t", prompt="search the web for tyre prices", status=TaskStatus.CREATED,
        tenant_id=TENANT, workspace_id=WORKSPACE, created_at=utc_now(), updated_at=utc_now(),
    )
    plain = asyncio.run(planner.plan(task))
    assert plain.plan.tool_calls[0].name == "web_search" and seen["route"] == ModelRoute.CAPABLE

    agent = _agent(instructions="Be brief.", model_route="fast")
    planned = asyncio.run(planner.plan(task, agent=agent))
    # The agent may not search the web: the skill is not selected and it answers in text.
    assert "anum.web-search" not in planned.plan.skill_ids
    assert planned.plan.tool_calls[0].name == "anum.respond"
    assert seen["route"] == ModelRoute.FAST
    assert "Be brief." in str(seen["prompt"]) and str(seen["prompt"]).endswith("search the web for tyre prices")


def test_retrieval_can_be_limited_to_an_agents_sources() -> None:
    context = ctx()
    embedder = LocalEmbedder()
    now = utc_now()
    index_store = InMemoryRetrievalStore()

    async def index(source_id: str, text: str) -> None:
        vector = (await embedder.embed([text])).vectors[0]
        index_store.save_source(
            context,
            RetrievalSource(
                tenant_id=TENANT, workspace_id=WORKSPACE, source_type=SourceType.FILE, source_id=source_id,
                content_sha256=sha256_text(text), status=IndexStatus.INDEXED, chunk_count=1,
                embedding_model=embedder.model_name, indexed_at=now,
            ),
            [ChunkInput(id=f"chunk_{source_id}", chunk_index=0, content=text, content_sha256=sha256_text(text), embedding=vector)],
        )

    asyncio.run(index("file_a", "tyre rotation schedule for trucks"))
    asyncio.run(index("file_b", "tyre rotation notes for vans"))
    retriever = Retriever(embedder, top_k=5, store_factory=lambda _: nullcontext(index_store))
    everything = asyncio.run(retriever.retrieve(context, "tyre rotation"))
    assert {chunk.source_id for chunk in everything.used} == {"file_a", "file_b"}
    only_b = asyncio.run(retriever.retrieve(context, "tyre rotation", sources=frozenset({("file", "file_b")})))
    assert {chunk.source_id for chunk in only_b.used} == {"file_b"}
    nothing = asyncio.run(retriever.retrieve(context, "tyre rotation", sources=frozenset()))
    assert nothing.used == []


# --------------------------------------------------------------------------- chat threads


def test_agent_chat_threads_are_separate_and_use_the_persona(client: TestClient) -> None:
    agent = _create(client, instructions="Speak like a pit crew chief.")
    client.post(f"/api/v1/agents/{agent['id']}/memories", headers=OWNER, json={"content": "Team colour is green"})
    general = client.post("/api/v1/conversations", headers=OWNER, json={}).json()
    thread = client.post("/api/v1/conversations", headers=OWNER, json={"agent_id": agent["id"]}).json()
    assert thread["agent_id"] == agent["id"] and general["agent_id"] is None
    assert [c["id"] for c in client.get("/api/v1/conversations", headers=OWNER).json()] == [general["id"]]
    assert [c["id"] for c in client.get(f"/api/v1/conversations?agent_id={agent['id']}", headers=OWNER).json()] == [
        thread["id"]
    ]
    reply = client.post(f"/api/v1/conversations/{thread['id']}/ask", headers=OWNER, json={"content": "Hello"})
    assert reply.status_code == 200 and "event: done" in reply.text

    client.patch(f"/api/v1/agents/{agent['id']}", headers=OWNER, json={"status": "paused"})
    assert client.post(f"/api/v1/conversations/{thread['id']}/ask", headers=OWNER, json={"content": "Hi"}).status_code == 409
    assert client.post("/api/v1/conversations", headers=OTHER, json={"agent_id": agent["id"]}).status_code == 404


def test_persona_comes_after_the_system_prompt() -> None:
    messages = build_messages([], "Hi", persona="You are Fleet, an agent in ANUM.")
    system = messages[0]["content"]
    assert system.index("You are Anum") < system.index("You are Fleet")


def test_catalog_lists_builtin_and_installed_skills(client: TestClient) -> None:
    org_admin = headers("user_admin", "owner,org_admin")
    published = client.post(
        "/api/v1/skills/versions",
        headers=org_admin,
        json={
            "skill_id": "acme.invoices", "version": "1.0.0", "name": "Invoices", "description": "Reads invoices",
            "instructions": "Read invoices", "required_tools": ["anum.respond"],
        },
    )
    assert published.status_code == 201, published.text
    installed = client.post(
        "/api/v1/skills/installations",
        headers=WS_OWNER,
        json={"skill_id": "acme.invoices", "version": "1.0.0", "approved_tools": ["anum.respond"]},
    )
    assert installed.status_code == 201
    try:
        skills = {s["id"]: s for s in client.get("/api/v1/agents/catalog", headers=OWNER).json()["skills"]}
        assert skills["acme.invoices"]["source"] == "installed"
        assert skills["anum.document-drafting"]["source"] == "builtin"
        assert "anum.web-search" not in skills  # its tool is not registered here
        agent = _create(client, allowed_skills=["acme.invoices"])
        client.patch(
            "/api/v1/skills/installations/acme.invoices", headers=WS_OWNER, json={"enabled": False}
        )
        assert client.get(f"/api/v1/agents/{agent['id']}", headers=OWNER).json()["unavailable_skills"] == [
            "acme.invoices"
        ]
    finally:
        from anum_api.skills_api import skill_store

        skill_store.clear()


def test_module_exposes_limits() -> None:
    assert agent_profiles.MAX_AGENTS_PER_WORKSPACE >= 50 and agent_profiles.MAX_AGENT_MEMORIES >= 100


def test_agent_category_is_the_stricter_of_risk_and_action_kind() -> None:
    from anum_api.agent_profiles import ToolCategory, agent_tool_category

    low_send = ToolDefinition(name="notify.send_text", description="x", risk_level=RiskLevel.LOW, action_kind="send")
    assert agent_tool_category("notify.send_text", low_send) == ToolCategory.EXTERNAL
    high_read = ToolDefinition(name="vault.read", description="x", risk_level=RiskLevel.HIGH, action_kind="read")
    assert agent_tool_category("vault.read", high_read) == ToolCategory.EXTERNAL
    draft = ToolDefinition(name="draft_reply", description="x", risk_level=RiskLevel.MEDIUM, action_kind="draft")
    assert agent_tool_category("draft_reply", draft) == ToolCategory.WRITE
    # Undeclared and unguessable: treated as acting outside ANUM.
    unknown = ToolDefinition(name="acme.frobnicate", description="x", risk_level=RiskLevel.LOW)
    assert agent_tool_category("acme.frobnicate", unknown) == ToolCategory.EXTERNAL


def test_agent_tightening_survives_autonomy_and_is_never_lowerable() -> None:
    lookup = ToolCall(name="tyrepulse.plan_rotation")
    definition = _registry_with_extra_tools().definition("tyrepulse.plan_rotation")
    acted = ToolPolicyDecision(
        outcome=ToolPolicyOutcome.ALLOW, reason="autonomy acts", risk_level=RiskLevel.LOW,
        autonomy={"mode": "notify", "guardrail": None, "proposable": True},
    )
    asked = apply_agent_policy(
        _agent(allowed_tools=["tyrepulse.plan_rotation"], autonomy=AgentAutonomy(read="ask")), lookup, definition, acted
    )
    assert asked.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL and asked.lowerable is False
    assert asked.autonomy is not None and asked.autonomy["mode"] == "ask" and asked.autonomy["proposable"] is False
    blocked = apply_agent_policy(_agent(), lookup, definition, acted)
    assert blocked.outcome == ToolPolicyOutcome.BLOCK and blocked.autonomy["mode"] == "block"


def test_email_template_uses_the_inbox_tools_when_connected() -> None:
    email = next(spec for spec in TEMPLATES if spec.id == "email")
    tools = {"anum.respond", "search_email", "read_email", "draft_reply", "send_email", "web_search"}
    resolved = resolve_template(email, tools, {"anum.document-drafting"})
    assert resolved.allowed_tools == ["draft_reply", "read_email", "search_email", "send_email"]
    assert resolved.missing_capabilities == []
    # Sending is an outward action: the template keeps it asking.
    assert resolved.autonomy.external == "ask"


def test_fleet_template_uses_the_tyre_planning_tools_when_connected() -> None:
    fleet = next(spec for spec in TEMPLATES if spec.id == "fleet")
    tools = {"anum.respond", "tyre_fleet_summary", "tyres_due", "purchase_plan", "tyre_alerts"}
    resolved = resolve_template(fleet, tools, {"anum.tyre-planning", "anum.document-drafting"})
    assert resolved.allowed_tools == ["purchase_plan", "tyre_alerts", "tyre_fleet_summary", "tyres_due"]
    assert resolved.allowed_skills == ["anum.tyre-planning", "anum.document-drafting"]
    assert resolved.missing_capabilities == []


def test_agent_threads_never_run_chat_tool_cards(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import chat_tools

    asked: list[str] = []
    monkeypatch.setattr(chat_tools, "chat_intent", lambda text, *_: asked.append(text) or None)
    assert chat_tools.configured() is not None  # main wires the chat task runtime
    agent = _create(client)
    general = client.post("/api/v1/conversations", headers=OWNER, json={}).json()
    thread = client.post("/api/v1/conversations", headers=OWNER, json={"agent_id": agent["id"]}).json()
    client.post(f"/api/v1/conversations/{general['id']}/ask", headers=OWNER, json={"content": "search the web"})
    reply = client.post(f"/api/v1/conversations/{thread['id']}/ask", headers=OWNER, json={"content": "search the web too"})
    assert reply.status_code == 200
    # Only the Assistant's own thread is considered for a tool card.
    assert asked == ["search the web"]


def test_every_state_changing_agent_route_is_audited_without_content(client: TestClient) -> None:
    from audit_probe import AuditProbe

    probe = AuditProbe(client, "agents")
    instructions = "Private instructions about the Amazon.sa listings"
    agent = probe.call(
        "POST", "/api/v1/agents", OWNER, sent=[instructions],
        json={"name": "Listings helper", "category": "work", "instructions": instructions},
    ).json()
    probe.call("PATCH", f"/api/v1/agents/{agent['id']}", OWNER, sent=["Check prices daily"], json={"instructions": "Check prices daily"})
    memory = probe.call(
        "POST", f"/api/v1/agents/{agent['id']}/memories", OWNER, sent=["The supplier is in Jeddah"],
        json={"content": "The supplier is in Jeddah"},
    ).json()
    probe.call("DELETE", f"/api/v1/agents/{agent['id']}/memories/{memory['id']}", OWNER)
    probe.call("DELETE", f"/api/v1/agents/{agent['id']}", OWNER)
    probe.assert_covered()
