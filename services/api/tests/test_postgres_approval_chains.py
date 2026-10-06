"""PostgreSQL: approval chains with more than two approvers (migration 0013).

The API runs on the test database as the non-owner app role, so every approver row is
written and read through the forced tenant/workspace RLS of ``approval_approvers``.
See docs/approvals-and-risk.md#approval-chains.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api import main
from anum_api.agent_tools import default_tool_registry
from anum_api.integration_tools import RestToolAdapter
from anum_api.main import app

from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database


def headers(user_id: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


REQUESTER = headers("user_test")


@pytest.fixture
def client(postgres_backend: None) -> TestClient:  # noqa: F811
    return TestClient(app)


def _waiting_approval(client: TestClient) -> dict:
    rule = client.post(
        "/api/v1/organization/approval-rules",
        headers=REQUESTER,
        json={"name": "Three approvers", "action_pattern": "external.*", "minimum_approvers": 3},
    )
    assert rule.status_code == 201, rule.text
    task = client.post("/api/v1/tasks", headers=REQUESTER, json={"title": "t", "prompt": "Publish the final update"})
    assert task.status_code == 201, task.text
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=REQUESTER)
    assert started.status_code == 200, started.text
    return started.json()["approval"]


def _approve(client: TestClient, approval: dict, user_id: str):
    return client.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        headers=headers(user_id),
        json={"payload_hash": approval["payload_hash"]},
    )


def _rows(database_engine: Engine, sql: str) -> list:
    with database_engine.connect() as connection:
        return list(connection.execute(text(sql)).all())


def test_a_chain_records_each_approver_and_resumes_on_the_last(
    client: TestClient, database_engine: Engine
) -> None:
    approval = _waiting_approval(client)
    # The run's own response already shows the chain ("0 of 3"), not the default 1.
    assert (approval["required_approvals"], approval["approvers"]) == (3, [])
    assert _approve(client, approval, "user_test").status_code == 403  # the requester never counts

    first = _approve(client, approval, "owner_b")
    assert first.status_code == 200, first.text
    assert first.json()["approval"]["status"] == "pending"
    assert first.json()["approval"]["required_approvals"] == 3
    assert _approve(client, approval, "owner_b").status_code == 409
    assert _approve(client, approval, "owner_c").json()["approval"]["status"] == "pending"

    listed = client.get(f"/api/v1/approvals/{approval['id']}", headers=REQUESTER).json()
    assert [item["user_id"] for item in listed["approvers"]] == ["owner_b", "owner_c"]
    assert client.get(f"/api/v1/tasks/{approval['task_id']}", headers=REQUESTER).json()["status"] == "waiting_approval"

    last = _approve(client, approval, "owner_d")
    assert last.status_code == 200, last.text
    assert last.json()["approval"]["status"] == "approved"
    assert last.json()["task"]["status"] == "completed"

    stored = _rows(
        database_engine,
        "select user_id, payload_hash, tenant_id, workspace_id from approval_approvers order by approved_at, user_id",
    )
    assert [row.user_id for row in stored] == ["owner_b", "owner_c", "owner_d"]
    assert {(row.payload_hash, row.tenant_id, row.workspace_id) for row in stored} == {
        (approval["payload_hash"], TENANT_A, WORKSPACE_A)
    }
    actions = [row.action for row in _rows(database_engine, "select action from audit_records order by created_at")]
    assert actions.count("approval.partially_approved") == 2
    assert actions.count("approval.approved") == 1
    events = [row.type for row in _rows(database_engine, "select type from domain_events order by created_at")]
    assert events.count("approval.partially_approved") == 2


def _webhook_registry(host: str) -> object:
    adapter = RestToolAdapter(
        endpoint=f"https://{host}/send",
        allowed_hosts={host},
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))),
    )
    return default_tool_registry(adapter)


def test_a_partial_approval_checks_the_integration_target(
    client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "tool_registry", _webhook_registry("hooks.example"))
    approval = _waiting_approval(client)
    assert approval["target"] == "hooks.example"
    assert _approve(client, approval, "owner_b").json()["approval"]["status"] == "pending"

    monkeypatch.setattr(main, "tool_registry", _webhook_registry("elsewhere.example"))
    refused = _approve(client, approval, "owner_c")
    assert refused.status_code == 200, refused.text
    assert refused.json()["approval"]["status"] == "expired"
    assert refused.json()["run"]["status"] == "failed"
    assert "integration target changed" in refused.json()["run"]["steps"][-1]["summary"]

    # Committed: only owner_b's approval was counted, the audit record names owner_c.
    assert [row.user_id for row in _rows(database_engine, "select user_id from approval_approvers")] == ["owner_b"]
    status = _rows(database_engine, "select status from approvals")
    assert [row.status for row in status] == ["expired"]
    mismatch = _rows(
        database_engine,
        "select actor, metadata from audit_records where action = 'approval.target_mismatch'",
    )
    assert [(row.actor, row.metadata["current_target"]) for row in mismatch] == [("owner_c", "elsewhere.example")]
    assert client.get(f"/api/v1/tasks/{approval['task_id']}", headers=REQUESTER).json()["status"] == "failed"
    assert _approve(client, approval, "owner_d").status_code == 410


def test_a_reject_ends_the_chain(client: TestClient) -> None:
    approval = _waiting_approval(client)
    assert _approve(client, approval, "owner_b").status_code == 200
    rejected = client.post(f"/api/v1/approvals/{approval['id']}/reject", headers=headers("owner_c"))
    assert rejected.status_code == 200
    assert rejected.json()["approval"]["status"] == "rejected"
    assert _approve(client, approval, "owner_d").status_code == 409


def test_approver_rows_are_isolated_by_tenant_and_workspace(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    approval = _waiting_approval(client)
    assert _approve(client, approval, "owner_b").status_code == 200

    for context in (tenant_context(), tenant_context(TENANT_A, WORKSPACE_A2), tenant_context(TENANT_B, WORKSPACE_B)):
        with app_session(context) as session:
            seen = session.execute(text("select count(*) from approval_approvers")).scalar_one()
        assert seen == (1 if context.workspace_id == WORKSPACE_A else 0)
    with app_session() as session:  # no tenant context at all
        assert session.execute(text("select count(*) from approval_approvers")).scalar_one() == 0

    # A row cannot be written into another workspace, nor point at another scope's approval.
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
            session.execute(
                text(
                    "insert into approval_approvers (tenant_id, workspace_id, approval_id, user_id, payload_hash, approved_at) "
                    "values (:tenant, :workspace, :approval, 'intruder', :hash, now())"
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A, "approval": approval["id"], "hash": approval["payload_hash"]},
            )
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
            session.execute(
                text(
                    "insert into approval_approvers (tenant_id, workspace_id, approval_id, user_id, payload_hash, approved_at) "
                    "values (:tenant, :workspace, :approval, 'intruder', :hash, now())"
                ),
                {"tenant": TENANT_B, "workspace": WORKSPACE_B, "approval": approval["id"], "hash": approval["payload_hash"]},
            )
