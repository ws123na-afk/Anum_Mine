"""Setup plans (docs/setup-plan.md), memory backend: preview, apply, idempotency, permissions,
tenant isolation, turned-off automations, starting autonomy and the audit record."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api import automation as automation_module
from anum_api.agent_profiles import agent_store
from anum_api.authorization import Permission
from anum_api.automation import LocalAutomationEngine
from anum_api.commands import memory_command_store
from anum_api.dependencies import memory_repository
from anum_api.governance import governance_store
from anum_api.knowledge_base import knowledge_store
from anum_api.main import app, store
from anum_api.model_budget import memory_model_budget_store as model_budget_store
from anum_api.schemas import WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.setup_plan_ledger import memory_setup_plan_ledger
from anum_api.setup_plans import load_blueprints
from anum_api.skills_api import skill_store
from anum_api.team_store import memory_team_store

TENANT, WORKSPACE = "tenant_setup", "workspace_setup"
PLAN = "shahzeb-business-group"


def h(user: str = "owner_1", roles: str = "owner,org_admin", workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


ADMIN = h()
OWNER = h("owner_2", "owner")
MEMBER = h("member_1", "member")


def _clear() -> None:
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()
    for cleared in (agent_store, governance_store, memory_team_store, memory_setup_plan_ledger, skill_store):
        cleared.clear()
    knowledge_store.clear()
    model_budget_store.clear()
    memory_command_store.clear()


def _member(user: str, role: str, workspace: str = WORKSPACE, tenant: str = TENANT) -> None:
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(tenant_id=tenant, workspace_id=workspace, user_id=user, role=role, created_at=now, updated_at=now)
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "automation.db")))
    _clear()
    test_client = TestClient(app)
    assert test_client.post("/api/v1/tenants", headers=ADMIN, json={"name": "Shahzeb Business Group"}).status_code == 201
    assert test_client.post("/api/v1/workspaces", headers=ADMIN, json={"name": "GROUP — Operations"}).status_code == 201
    assert test_client.post("/api/v1/workspace-memberships/current", headers=ADMIN).status_code == 201
    _member("owner_2", "owner")
    _member("member_1", "member")
    yield test_client
    _clear()


def preview(client: TestClient, phase: str = "pilot", headers: dict[str, str] = ADMIN, **params: str) -> dict:
    response = client.get(f"/api/v1/setup-plans/{PLAN}/preview", headers=headers, params={"phase": phase, **params})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client: TestClient, phase: str = "pilot", headers: dict[str, str] = ADMIN, **extra: object) -> dict:
    response = client.post(f"/api/v1/setup-plans/{PLAN}/apply", headers=headers, json={"phase": phase, "plan_version": 1, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def items(result: dict, section: str) -> list[dict]:
    return [item for item in result["items"] if item["section"] == section]


def agents(client: TestClient) -> dict[str, dict]:
    page = client.get("/api/v1/agents?limit=100", headers=ADMIN).json()
    return {agent["name"]: agent for agent in page["items"]}


# ---------------------------------------------------------------------------- blueprint


def test_blueprint_holds_the_whole_plan_with_real_names() -> None:
    blueprint = load_blueprints()[PLAN]
    assert (len(blueprint.agents), len(blueprint.skills), len(blueprint.assistants)) == (12, 24, 8)
    assert (len(blueprint.policy_packs), len(blueprint.saved_commands), len(blueprint.automations)) == (9, 30, 25)
    assert len(blueprint.knowledge_folders) == 9 and len(blueprint.workspaces) == 5
    assert blueprint.budget_aed(blueprint_phase("all")) == 1200 and blueprint.budget_aed(blueprint_phase("pilot")) == 340
    permissions = {p.value for p in Permission}
    for template in blueprint.role_templates:
        assert set(template.permissions) <= permissions, template.name
    for agent in blueprint.agents:
        assert len(agent.instructions) <= 8000 and agent.instructions.startswith("You work for Shahzeb Rahman inside ANUM.")
    text = Path(__file__).parents[1].joinpath("anum_api/blueprints/shahzeb_business_group.json").read_text(encoding="utf-8").lower()
    for secret_word in ("password\":", "api_key", "\"sk-", " sk-", "bearer "):
        assert secret_word not in text


def test_blueprints_ship_with_the_package() -> None:
    pyproject = Path(__file__).parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    package_data = next(line for line in pyproject.splitlines() if line.startswith("anum_api = ["))
    assert '"blueprints/*.json"' in package_data


def blueprint_phase(value: str):  # type: ignore[no-untyped-def]
    from anum_api.setup_plans import Phase

    return Phase(value)


# ---------------------------------------------------------------------------- preview


def test_preview_is_read_only_and_explains_every_item(client: TestClient) -> None:
    result = preview(client)
    assert result["plan"]["applied_items"] == 0 and result["phase"] == "pilot"
    assert result["budget_aed"] == 340 and result["budget_usd"] == pytest.approx(92.58)
    by_action = {item["action"] for item in result["items"]}
    assert {"create", "later", "builtin", "unsupported", "manual"} <= by_action
    assert all(item["reason"] for item in result["items"])
    assert all(item["action"] == "later" for item in items(result, "workspaces"))
    assert not agent_store.profiles and not memory_setup_plan_ledger.rows
    assert not [r for r in store.audit_records if r.action.startswith("setup_plan")]
    # The plan lists what ANUM cannot do, honestly.
    unsupported = {item["key"] for item in items(result, "unsupported")}
    assert {"u_banking", "u_courier", "u_per_agent_budget"} <= unsupported


def test_member_and_viewer_are_refused_with_who_can(client: TestClient) -> None:
    for path in ("", f"/{PLAN}/preview"):
        response = client.get(f"/api/v1/setup-plans{path}", headers=MEMBER)
        assert response.status_code == 403 and "workspace owner or an organization administrator" in response.json()["error"]["message"]
    response = client.post(f"/api/v1/setup-plans/{PLAN}/apply", headers=MEMBER, json={"phase": "pilot", "plan_version": 1})
    assert response.status_code == 403
    assert not agent_store.profiles


def test_unknown_plan_and_stale_version(client: TestClient) -> None:
    assert client.get("/api/v1/setup-plans/nope/preview", headers=ADMIN).status_code == 404
    response = client.post(f"/api/v1/setup-plans/{PLAN}/apply", headers=ADMIN, json={"phase": "pilot", "plan_version": 7})
    assert response.status_code == 409 and not agent_store.profiles


# ---------------------------------------------------------------------------- apply


def test_apply_pilot_creates_everything_with_pilot_agents_active(client: TestClient) -> None:
    result = apply(client)
    failed = [item for item in result["items"] if item["outcome"] == "failed"]
    assert not failed, failed
    by_name = agents(client)
    assert len(by_name) == 12
    active = {name for name, agent in by_name.items() if agent["status"] == "active"}
    assert active == {"GROUP — Chief of Staff", "GALLERY — Customer Care", "GROUP — Finance Controller"}
    care = by_name["GALLERY — Customer Care"]
    # Observe: reads on their own, changes ask, nothing outside ANUM.
    assert care["autonomy"] == {"read": "auto", "write": "ask", "external": "off"}
    assert care["sharing"] == "workspace" and care["category"] == "work" and care["department"] == "Commerce & Care"
    assert "sbg.s17-verified-support-reply" in care["allowed_skills"] and "send_whatsapp" not in care["allowed_tools"]
    chief = by_name["GROUP — Chief of Staff"]
    assert chief["autonomy"] == {"read": "auto", "write": "auto", "external": "ask"}
    # Skills published, installed with only their declared tools.
    installs = client.get("/api/v1/skills/installations", headers=ADMIN).json()
    assert len([i for i in installs if i["skill_id"].startswith("sbg.")]) == 24
    # Governance through the governance routes.
    packs = client.get("/api/v1/policy-packs", headers=ADMIN).json()
    assert len(packs) == 6 and all(pack["active"] for pack in packs)
    assert len(client.get("/api/v1/organization/approval-rules", headers=ADMIN).json()) == 4
    assert len(client.get("/api/v1/role-templates", headers=ADMIN).json()) == 10
    memory = client.get("/api/v1/organization/memory-governance", headers=ADMIN).json()
    assert memory["default_retention_days"] == 90 and memory["allow_permanent_retention"] is False
    # Assistants created turned off in the pilot.
    assistants = client.get("/api/v1/assistants", headers=ADMIN).json()
    assert len(assistants) == 8 and not any(a["enabled"] for a in assistants)
    assert all(a["autonomy"] == "suggest" for a in assistants)
    # Knowledge folders, saved commands, budget.
    folders = client.get("/api/v1/knowledge/collections", headers=ADMIN).json()
    assert sorted(c["name"] for c in folders)[0] == "00 Governance" and len(folders) == 9
    quick = client.get("/api/v1/quick-commands", headers=ADMIN).json()
    assert len(quick) == 30 and all(q["category"] == "work" for q in quick)
    budgets = client.get("/api/v1/model-budgets", headers=ADMIN).json()
    assert budgets["workspace"]["budget"]["monthly_cost_limit_usd"] == pytest.approx(92.58)
    assert result["outcomes"]["created"] > 100


def test_automations_are_created_turned_off(client: TestClient) -> None:
    apply(client)
    workflows = client.get("/api/v1/automation/workflows?limit=200", headers=ADMIN).json()
    assert len(workflows) == 18
    assert all(w["status"] == "disabled" for w in workflows)
    schedules = client.get("/api/v1/automation/schedules", headers=ADMIN).json()
    assert schedules and not any(s["enabled"] for s in schedules)
    assert all(s["timezone"] == "Asia/Dubai" for s in schedules)
    triggers = client.get("/api/v1/automation/triggers", headers=ADMIN).json()
    assert triggers and not any(t["enabled"] for t in triggers) and all(t["kind"] == "event" for t in triggers)
    brief = next(s for s in schedules if s["name"] == "GROUP — Morning Brief")
    assert brief["cron"] == "30 8 * * 1-5" and brief["next_run_at"] is None


def test_reapply_never_duplicates_and_audits_each_apply(client: TestClient) -> None:
    apply(client)
    second = apply(client)
    assert not second["outcomes"].get("created") and not second["outcomes"].get("failed")
    assert len(agent_store.profiles) == 12
    assert len(client.get("/api/v1/automation/workflows?limit=200", headers=ADMIN).json()) == 18
    assert len(client.get("/api/v1/quick-commands", headers=ADMIN).json()) == 30
    assert len(client.get("/api/v1/policy-packs", headers=ADMIN).json()) == 6
    third = preview(client)
    assert not [i for i in third["items"] if i["action"] in {"create", "update"}]
    records = [r for r in store.audit_records if r.action == "setup_plan.applied"]
    assert len(records) == 2 and records[0].target == f"setup_plan:{PLAN}"
    assert records[0].metadata["phase"] == "pilot" and "outcomes" in records[0].metadata


def test_apply_all_after_pilot_activates_untouched_items_and_keeps_edits(client: TestClient) -> None:
    apply(client)
    by_name = agents(client)
    edited = by_name["FLEET — Sales Pipeline"]
    response = client.patch(f"/api/v1/agents/{edited['id']}", headers=ADMIN, json={"description": "My own words", "version": edited["version"]})
    assert response.status_code == 200
    result = apply(client, "all")
    assert not [i for i in result["items"] if i["outcome"] == "failed"]
    by_name = agents(client)
    assert by_name["FLEET — Sales Pipeline"]["status"] == "paused"  # owner's edit kept
    assert by_name["FLEET — Sales Pipeline"]["description"] == "My own words"
    kept = next(i for i in result["items"] if i["key"] == "a2")
    assert kept["action"] == "kept" and kept["reason_code"] == "edited_by_owner"
    assert all(agent["status"] == "active" for name, agent in by_name.items() if name != "FLEET — Sales Pipeline")
    assert all(a["enabled"] for a in client.get("/api/v1/assistants", headers=ADMIN).json())
    budgets = client.get("/api/v1/model-budgets", headers=ADMIN).json()
    assert budgets["workspace"]["budget"]["monthly_cost_limit_usd"] == pytest.approx(326.75)
    # Automations stay turned off whatever the phase.
    assert all(w["status"] == "disabled" for w in client.get("/api/v1/automation/workflows?limit=200", headers=ADMIN).json())
    # Five business workspaces, created once.
    assert {i["outcome"] for i in items(result, "workspaces")} == {"created"}
    again = apply(client, "all")
    assert {i["outcome"] for i in items(again, "workspaces")} == {"present"}
    # "Replace my changes" restores the plan's version.
    replaced = apply(client, "all", overwrite_edited=True)
    assert next(i for i in replaced["items"] if i["key"] == "a2")["outcome"] == "updated"
    assert agents(client)["FLEET — Sales Pipeline"]["status"] == "active"


def test_autonomy_is_never_above_the_plans_starting_level(client: TestClient) -> None:
    apply(client, "all")
    blueprint = load_blueprints()[PLAN]
    by_name = agents(client)
    for agent in blueprint.agents:
        autonomy = by_name[agent.name]["autonomy"]
        assert autonomy["external"] != "auto"
        if agent.autonomy == "observe":
            assert autonomy == {"read": "auto", "write": "ask", "external": "off"}, agent.name
        else:
            assert autonomy == {"read": "auto", "write": "auto", "external": "ask"}, agent.name


def test_owner_without_org_admin_skips_governance_with_the_reason(client: TestClient) -> None:
    result = apply(client, headers=OWNER)
    governance = items(result, "governance")
    skipped = [i for i in governance if i["action"] == "skipped"]
    assert skipped and all(i["reason_code"] == "needs_org_admin" for i in skipped)
    assert client.get("/api/v1/policy-packs", headers=OWNER).json() == []
    publish = [i for i in items(result, "skills") if i["kind"] == "skill_version"]
    assert all(i["action"] == "skipped" for i in publish)
    assert result["access"]["organization_admin"] is False and result["access"]["notes"]
    # Agents are still created, with built-in skills only.
    care = agents(client)["GALLERY — Customer Care"]
    assert not [s for s in care["allowed_skills"] if s.startswith("sbg.")]
    # An organization administrator applying later adds governance and skills; agents gain them.
    later = apply(client)
    assert any(i["outcome"] == "created" for i in items(later, "governance"))
    upgraded = next(i for i in later["items"] if i["key"] == "a7")
    assert upgraded["outcome"] == "updated"


def test_found_by_name_is_linked_not_changed(client: TestClient) -> None:
    created = client.post("/api/v1/knowledge/collections", headers=ADMIN, json={"name": "02 SOPs", "description": "mine"})
    assert created.status_code == 201
    result = apply(client)
    folder = next(i for i in result["items"] if i["key"] == "k02")
    assert folder["action"] == "present" and folder["reason_code"] == "found_by_name"
    folders = client.get("/api/v1/knowledge/collections", headers=ADMIN).json()
    assert len([c for c in folders if c["name"] == "02 SOPs"]) == 1
    assert next(c for c in folders if c["name"] == "02 SOPs")["description"] == "mine"


def test_other_tenant_and_workspace_are_isolated(client: TestClient) -> None:
    apply(client)
    other = h("owner_b", "owner,org_admin", workspace="workspace_b", tenant="tenant_b")
    assert client.post("/api/v1/tenants", headers=other, json={"name": "Other"}).status_code == 201
    assert client.post("/api/v1/workspaces", headers=other, json={"name": "B"}).status_code == 201
    assert client.post("/api/v1/workspace-memberships/current", headers=other).status_code == 201
    result = preview(client, headers=other)
    assert result["plan"]["applied_items"] == 0
    assert all(i["action"] in {"create", "later", "builtin", "unsupported", "manual"} for i in result["items"]), [
        (i["key"], i["action"]) for i in result["items"] if i["action"] not in {"create", "later", "builtin", "unsupported", "manual"}
    ]
    assert client.get("/api/v1/agents", headers=other).json()["items"] == []
    # A second workspace of the same tenant sees the tenant's governance as applied.
    second = h("owner_1", "owner,org_admin", workspace="workspace_setup_two")
    assert client.post("/api/v1/workspaces", headers=second, json={"name": "Two"}).status_code == 201
    assert client.post("/api/v1/workspace-memberships/current", headers=second).status_code == 201
    there = preview(client, headers=second)
    assert {i["action"] for i in items(there, "governance") if i["kind"] != "policy_pack" or i["action"] != "builtin"} == {"present"}
    assert {i["action"] for i in items(there, "agents")} == {"create"}


def _claim(key: str, minutes_old: int) -> None:
    from datetime import timedelta

    from anum_api.setup_plan_ledger import LedgerEntry, LedgerState

    when = utc_now() - timedelta(minutes=minutes_old)
    memory_setup_plan_ledger.save(LedgerEntry(
        tenant_id=TENANT, workspace_id=WORKSPACE, plan_id=PLAN, item_key=key, kind="agent", state=LedgerState.CLAIMED,
        applied_by="someone", created_at=when, updated_at=when,
    ))


def test_a_fresh_claim_is_not_created_twice_and_a_stale_one_is_retaken(client: TestClient) -> None:
    _claim("a1", minutes_old=0)
    _claim("a7", minutes_old=30)
    result = apply(client)
    chief = next(i for i in result["items"] if i["key"] == "a1")
    care = next(i for i in result["items"] if i["key"] == "a7")
    assert chief["action"] == "skipped" and chief["reason_code"] == "busy"
    assert care["outcome"] == "created"
    names = agents(client)
    assert "GROUP — Chief of Staff" not in names and "GALLERY — Customer Care" in names


def test_a_failed_item_is_reported_released_and_created_by_the_next_apply(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import knowledge_base

    real = knowledge_base.create_collection

    async def broken(payload, context):  # type: ignore[no-untyped-def]
        if payload.name == "05 Finance":
            raise RuntimeError("store unavailable")
        return await real(payload, context)

    monkeypatch.setattr(knowledge_base, "create_collection", broken)
    result = apply(client)
    failed = next(i for i in result["items"] if i["key"] == "k05")
    assert failed["outcome"] == "failed" and "store unavailable" in failed["error"]
    assert result["outcomes"]["failed"] == 1
    record = next(r for r in store.audit_records if r.action == "setup_plan.applied")
    assert record.outcome == "partial"
    monkeypatch.setattr(knowledge_base, "create_collection", real)
    again = apply(client)
    assert next(i for i in again["items"] if i["key"] == "k05")["outcome"] == "created"
    assert len(client.get("/api/v1/knowledge/collections", headers=ADMIN).json()) == 9


def test_a_removed_item_stays_removed_unless_replaced(client: TestClient) -> None:
    apply(client)
    folder = next(c for c in client.get("/api/v1/knowledge/collections", headers=ADMIN).json() if c["name"] == "07 Marketing")
    assert client.delete(f"/api/v1/knowledge/collections/{folder['id']}", headers=ADMIN).status_code == 204
    result = preview(client)
    item = next(i for i in result["items"] if i["key"] == "k07")
    assert item["action"] == "kept" and item["reason_code"] == "removed_by_owner"
    replaced = apply(client, overwrite_edited=True)
    assert next(i for i in replaced["items"] if i["key"] == "k07")["outcome"] == "created"
