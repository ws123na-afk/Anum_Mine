"""PostgreSQL: governance approval rules and policy packs in the tool policy (threat model A4).

The whole API (tasks, runs, approvals and the governance store) runs on the test database
as the non-owner app role, so every read of the rules goes through tenant RLS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.main import app

from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "user_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


A = headers()
A_OTHER_OWNER = headers(user_id="owner_other")
B = headers(TENANT_B, WORKSPACE_B)


@pytest.fixture
def client(postgres_backend: None) -> TestClient:  # noqa: F811
    return TestClient(app)


def _run(client: TestClient, prompt: str, who: dict[str, str] = A) -> dict:
    task = client.post("/api/v1/tasks", headers=who, json={"title": "t", "prompt": prompt})
    assert task.status_code == 201, task.text
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=who)
    assert started.status_code == 200, started.text
    return started.json()


def _rule(client: TestClient, pattern: str, who: dict[str, str] = A, **extra: object) -> dict:
    response = client.post(
        "/api/v1/organization/approval-rules",
        headers=who,
        json={"name": f"rule {pattern}", "action_pattern": pattern, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _audit_actions(database_engine: Engine) -> list[str]:
    with database_engine.connect() as connection:
        return list(connection.execute(text("select action from audit_records order by created_at")).scalars())


def test_an_approval_rule_pauses_runs_of_its_tenant_only(client: TestClient) -> None:
    _rule(client, "anum.respond")

    body = _run(client, "Write a short note")
    assert body["task"]["status"] == "waiting_approval"
    assert body["approval"]["risk_level"] == "low"
    assert "organization approval rule" in body["approval"]["reason"]
    proposal = next(step for step in body["run"]["steps"] if step["type"] == "tool_proposal")
    assert proposal["metadata"]["governance_rules"] == ["approval_rule:rule anum.respond"]

    assert _run(client, "Write a short note", B)["task"]["status"] == "completed"

    approval = body["approval"]
    approved = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["task"]["status"] == "completed"


def test_two_approver_rule_is_enforced_and_audited(client: TestClient, database_engine: Engine) -> None:
    _rule(client, "external.*", minimum_approvers=2)
    approval = _run(client, "Publish the final update")["approval"]
    body = {"payload_hash": approval["payload_hash"]}

    refused = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json=body)
    assert refused.status_code == 403
    assert client.get(f"/api/v1/approvals/{approval['id']}", headers=A).json()["status"] == "pending"
    assert "approval.self_approval_denied" in _audit_actions(database_engine)

    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A_OTHER_OWNER, json=body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["decided_by"] == "owner_other"


def test_an_active_deny_pack_blocks_and_archiving_it_lifts_the_block(client: TestClient) -> None:
    pack = client.post(
        "/api/v1/policy-packs",
        headers=A,
        json={"name": "No notes", "description": "", "rules": [{"action": "anum.*", "effect": "deny"}]},
    )
    assert pack.status_code == 201, pack.text
    blocked = _run(client, "Write a short note")
    assert blocked["task"]["status"] == "failed"
    assert "policy pack denies" in blocked["run"]["steps"][-1]["summary"]
    proposal = next(step for step in blocked["run"]["steps"] if step["type"] == "tool_proposal")
    assert proposal["metadata"]["governance_rules"] == ["policy_pack:No notes@v1:anum.*"]

    archived = client.post(f"/api/v1/policy-packs/{pack.json()['id']}/archive", headers=A)
    assert archived.status_code == 200
    assert _run(client, "Write a short note")["task"]["status"] == "completed"


def test_repository_reads_rules_under_tenant_rls(
    client: TestClient,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
    app_session: Callable[..., Iterator[Session]],
) -> None:
    _rule(client, "external.*", required_roles=["owner"])
    _rule(client, "risk:medium", who=B)
    created = client.post(
        "/api/v1/policy-packs",
        headers=A,
        json={"name": "Hosts", "description": "", "rules": [{"action": "target:*.example", "effect": "require_approval"}]},
    )
    assert created.status_code == 201

    context_a = tenant_context(TENANT_A, WORKSPACE_A)
    with repository_factory(context_a) as repository:
        governance = repository.get_tool_governance(context_a)
    assert [rule.action_pattern for rule in governance.approval_rules] == ["external.*"]
    assert [(rule.pack_name, rule.pack_version, rule.effect) for rule in governance.policy_rules] == [
        ("Hosts", 1, "require_approval")
    ]

    # Asking for tenant B's rules from a tenant A session returns nothing: RLS, not the
    # query's own filter, decides.
    context_b = tenant_context(TENANT_B, WORKSPACE_B)
    with repository_factory(context_a) as repository:
        assert repository.get_tool_governance(context_b).approval_rules == []
    with repository_factory(context_b) as repository:
        assert [rule.action_pattern for rule in repository.get_tool_governance(context_b).approval_rules] == [
            "risk:medium"
        ]

    # Disabled rules are not consulted.
    with app_session(context_a, commit=True) as session:
        assert session.execute(text("update approval_rules set enabled = false")).rowcount == 1
    with repository_factory(context_a) as repository:
        assert repository.get_tool_governance(context_a).approval_rules == []
