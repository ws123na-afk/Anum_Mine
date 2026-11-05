"""Office staffing blueprint: integrity of the reviewed data, matching, gaps, hashes, and the route guards."""
from __future__ import annotations

import re

from anum_api import agent_profiles as ap
from anum_api import main  # noqa: F401 (registers the runtime catalogue and the routes)
from anum_api import office_staffing_blueprint as bp
from anum_api import office_starter_campus as campus
from anum_api.executive_pa import TOOLS as PA_TOOLS
from anum_api.office_green_concrete import BUILDING_ID as GC_BUILDING, PEOPLE
from anum_api.office_layout import TEAM_DEPARTMENTS
from anum_api.schemas import TenantContext
from fastapi.testclient import TestClient

ARABIC = re.compile(r"[؀-ۿ]")
CTX = TenantContext(tenant_id="t", workspace_id="w", user_id="u", roles=["owner"])
client = TestClient(main.app)


def _caps():
    from anum_api import office_staffing_api as api

    return api._capabilities(CTX)


def test_every_tool_and_skill_of_every_role_exists_in_the_registry_and_catalog() -> None:
    registry = ap.runtime_tools()
    skills = {s.id for s in ap.catalog_skills(CTX)}
    for role in bp.ROLES:
        for tool in role.template.tools:
            assert registry.definition(tool) is not None, f"{role.key}: {tool} is not registered"
        for skill in role.template.skills:
            assert skill in skills, f"{role.key}: {skill} is not in the skills catalog"


def test_no_role_has_a_tool_above_its_allowed_risk_and_none_allows_high() -> None:
    registry = ap.runtime_tools()
    for role in bp.ROLES:
        assert role.max_risk in ("low", "medium"), role.key
        for tool in role.template.tools:
            risk = registry.definition(tool).risk_level.value
            assert bp.RISK_ORDER[risk] <= bp.RISK_ORDER[role.max_risk], f"{role.key}: {tool} is {risk}"
        view = bp.role_view(role, *_caps())
        assert not [t for t in view["tools"] if t["state"] == "over_risk"], role.key


def test_every_role_has_rules_sources_and_arabic_for_every_string() -> None:
    for role in bp.ROLES:
        assert len(role.key) <= 22 and re.fullmatch(r"[a-z][a-z0-9_]*", role.key)
        assert set(bp.BASE_RULES) <= set(role.rules)
        assert all(r in bp.RULE_BY_ID for r in role.rules), role.key
        assert role.sources and all(s in bp.SOURCE_BY_ID for s in role.sources), role.key
        assert ARABIC.search(role.purpose_ar) and role.purpose_en
        assert bp.autonomy(role)["external"] == "off" and bp.autonomy(role)["write"] in ("ask", "off")
        assert role.template.monthly_cost_limit_usd > 0
        assert len(bp.marker("campus-04-corporate-services", role.key)) <= 40
        assert bp.RULES_MARKER in bp.instructions(role) and len(bp.instructions(role)) <= 8000
    for item in (*bp.SOURCES, *bp.RULES, *bp.UNITS, *bp.OFFICE_KINDS):
        assert ARABIC.search(item.ar) and item.en, item.id


def test_finance_roles_keep_live_trading_off_and_no_role_sends() -> None:
    for role in bp.ROLES:
        if role.template_id == "finance_reconciliation":
            assert {"no_live_trading", "no_money_movement"} <= set(role.rules)
        assert "no_outward_without_approval" in role.rules


def test_no_dashes_and_no_model_names_in_the_blueprint() -> None:
    text = repr(bp.blueprint_document()) + repr([(r.purpose_en, r.purpose_ar) for r in bp.ROLES])
    assert "–" not in text and "—" not in text
    assert not any(w in text.lower() for w in ("claude", "gpt", "opus", "sonnet", "haiku"))


def test_units_kinds_and_roles_are_consistent() -> None:
    used = {k for u in bp.UNITS for k in u.roles}
    assert used == set(bp.ROLE_BY_KEY)
    assert len({r.key for r in bp.ROLES}) == len(bp.ROLES)
    for kind in bp.OFFICE_KINDS:
        assert all(u in bp.UNIT_BY_ID for u in kind.units)
    for dept, unit in bp.DEPARTMENT_UNITS.items():
        assert dept in TEAM_DEPARTMENTS and unit in bp.UNIT_BY_ID


def test_every_starter_building_and_green_concrete_has_a_blueprint() -> None:
    for spec in campus.CAMPUS:
        kind, units = bp.classify(spec.id, [])
        assert kind not in ("custom", "general"), spec.id
        assert bp.roles_for(units)
    assert bp.classify(GC_BUILDING, [])[0] == "green_concrete"
    # The owner's people are matched by the role stored as their department.
    roles = bp.roles_for(bp.classify(GC_BUILDING, [])[1])
    agents = [_agent(p.key, department=p.role_en) for p in PEOPLE]
    by_role, others = bp.match_agents(GC_BUILDING, roles, agents)
    assert not others and all(by_role[r.key] for r in roles)
    assert len(by_role["gc_data_monitoring"]) == 4 and len(by_role["gc_data_collector"]) == 3


def test_other_buildings_are_classified_from_their_rooms() -> None:
    rooms = [{"id": "r1", "kind": "office", "team_department": "finance"}, {"id": "r2", "kind": "reception", "team_department": None},
             {"id": "r3", "kind": "office", "team_department": "Legal"}]
    kind, units = bp.classify("my-own", rooms)
    assert kind == "custom" and units == ("reception", "finance", "legal_compliance")
    assert bp.classify("empty", []) == ("general", ("operations",))


def _agent(aid: str, **kw) -> bp.OfficeAgent:
    base = dict(id=aid, name=aid, status="active", department=None, template_id=None, allowed_tools=(), allowed_skills=(),
                autonomy={"read": "auto", "write": "ask", "external": "off"}, has_rules=False, cap_usd=None, can_edit=True,
                version=1, desk_id=None)
    return bp.OfficeAgent(**{**base, **kw})


def test_matching_prefers_marker_then_template_then_department() -> None:
    roles = bp.roles_for(("commerce",))
    agents = [_agent("a1", department="operations"), _agent("a2", template_id="hq:support_triage:abc"),
              _agent("a3", template_id=bp.marker("b", "sales_followup"), department="support"), _agent("a4", department="legal")]
    by_role, others = bp.match_agents("b", roles, agents)
    assert [a.id for a in by_role["orders_desk"]] == ["a1"]
    assert [a.id for a in by_role["support_triage"]] == ["a2"]
    assert [a.id for a in by_role["sales_followup"]] == ["a3"]
    assert [a.id for a in others] == ["a4"]


def test_gaps_list_missing_skills_tools_rules_and_over_risk_tools() -> None:
    role = bp.ROLE_BY_KEY["finance_clerk"]
    view = bp.role_view(role, *_caps())
    agent = _agent("f", allowed_tools=("finance.summary", "send_email"), autonomy={"read": "auto", "write": "auto", "external": "ask"})
    gaps = bp.agent_gaps(role, view, agent, {"finance.summary": "low", "send_email": "high"})
    assert "finance.summary" not in gaps["missing_tools"] and "finance.transactions.search" in gaps["missing_tools"]
    assert "anum.finance" in gaps["missing_skills"]
    assert set(gaps["missing_rules"]) == {"autonomy", "cost_cap", "never_list"}
    assert gaps["over_risk_tools"] == ["send_email"] and not gaps["complete"]
    assert bp.tightened(agent.autonomy, role) == {"read": "auto", "write": "off", "external": "off"}
    # Tightening never loosens: an agent stricter than its role stays as it is.
    assert bp.tightened({"read": "off", "write": "off", "external": "off"}, bp.ROLE_BY_KEY["orders_desk"])["read"] == "off"
    done = _agent("g", allowed_tools=tuple(view["attach_tools"]), allowed_skills=tuple(view["attach_skills"]),
                  autonomy=bp.autonomy(role), has_rules=True, cap_usd=2.0)
    assert bp.agent_gaps(role, view, done, {})["complete"]


def test_action_hash_binds_tenant_workspace_user_office_and_content() -> None:
    base = bp.action_hash("t", "w", "u", "b", {"x": 1})
    assert base != bp.action_hash("t2", "w", "u", "b", {"x": 1})
    assert base != bp.action_hash("t", "w2", "u", "b", {"x": 1})
    assert base != bp.action_hash("t", "w", "u2", "b", {"x": 1})
    assert base != bp.action_hash("t", "w", "u", "b2", {"x": 1})
    assert base != bp.action_hash("t", "w", "u", "b", {"x": 2})


def test_no_registered_tool_or_assistant_tool_applies_a_blueprint() -> None:
    assert not [n for n in ap.runtime_tools().names if "staffing" in n or "blueprint" in n]
    assert not [n for n in PA_TOOLS if "staffing" in n or "blueprint" in n]


def headers(role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": "tenant-a", "x-workspace-id": "workspace-a", "x-user-id": "user-a", "x-user-roles": role}


def test_routes_need_postgres_and_permissions() -> None:
    assert client.get("/api/v1/office/staffing/blueprint", headers=headers("viewer")).status_code == 200
    assert client.get("/api/v1/office/staffing/offices", headers=headers()).status_code == 503
    body = {"input_mode": "click", "actions": [{"id": "create:orders_desk", "hash": "0" * 64}]}
    assert client.post("/api/v1/office/staffing/offices/b/apply", headers=headers("viewer"), json=body).status_code == 403
    assert client.post("/api/v1/office/staffing/offices/b/apply", headers=headers("member"), json=body).status_code == 403
    assert client.post("/api/v1/office/staffing/offices/b/apply", headers=headers(), json=body).status_code == 503
    too_many = {"input_mode": "click", "actions": [{"id": f"create:r{i}", "hash": "0" * 64} for i in range(21)]}
    assert client.post("/api/v1/office/staffing/offices/b/apply", headers=headers(), json=too_many).status_code == 422


def test_blueprint_route_returns_every_role_resolved() -> None:
    body = client.get("/api/v1/office/staffing/blueprint", headers=headers("viewer")).json()
    assert body["version"] == bp.BLUEPRINT_VERSION and len(body["roles"]) == len(bp.ROLES)
    role = next(r for r in body["roles"] if r["key"] == "orders_desk")
    assert role["rules"]["autonomy"]["external"] == "off" and role["rules"]["never"]
    assert all(t["risk"] in ("low", "medium") for t in role["tools"])


def test_source_probe_binds_the_tenant_and_quotes_the_table_name() -> None:
    """The Connected probe is built by SQLAlchemy, never by formatting a string (bandit B608)."""
    from sqlalchemy.dialects import postgresql

    from anum_api import office_staffing_api as api

    for source in bp.SOURCES:
        for name in source.tables:
            compiled = api._row_probe(name, {"t": "tenant-1", "w": "ws-1"}).compile(dialect=postgresql.dialect())
            sql = str(compiled)
            assert f"FROM {name}" in sql
            assert "tenant-1" not in sql and "ws-1" not in sql
            assert {"tenant-1", "ws-1"} <= set(compiled.params.values())
            assert "LIMIT" in sql
    hostile = api._row_probe("x; drop table y", {"t": "t", "w": "w"}).compile(dialect=postgresql.dialect())
    assert 'FROM "x; drop table y"' in str(hostile)
