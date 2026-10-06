"""PostgreSQL: decision reasons, requester, target and the per-workspace approval policy
(migration 0012_approval_policy, docs/approvals-and-risk.md)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import WorkspaceApprovalPolicy

from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from test_postgres_runtime import HEADERS, postgres_client  # noqa: F401 (fixture)

pytestmark = pytest.mark.database

OTHER_OWNER = {**HEADERS, "x-user-id": "owner_other"}


def _waiting(client: TestClient) -> dict:
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Publish", "prompt": "Publish the final update"})
    assert task.status_code == 201
    assert task.json()["created_by"] == HEADERS["x-user-id"]
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=HEADERS)
    assert started.status_code == 200
    return started.json()["approval"]


def test_two_person_rule_and_reason_persist(
    postgres_client: TestClient,  # noqa: F811
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    client = postgres_client
    updated = client.put(
        "/api/v1/approval-policy",
        headers=HEADERS,
        json={"two_person_rule": True, "medium_risk_requires_approval": False},
    )
    assert updated.status_code == 200
    assert client.get("/api/v1/approval-policy", headers=HEADERS).json()["two_person_rule"] is True

    approval = _waiting(client)
    assert approval["requested_by"] == HEADERS["x-user-id"]
    body = {"payload_hash": approval["payload_hash"], "reason": "self"}
    refused = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=HEADERS, json=body)
    assert refused.status_code == 403
    assert client.get(f"/api/v1/approvals/{approval['id']}", headers=HEADERS).json()["status"] == "pending"

    body["reason"] = "Reviewed by a second owner"
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OTHER_OWNER, json=body)
    assert approved.status_code == 200, approved.text
    stored = client.get(f"/api/v1/approvals/{approval['id']}", headers=HEADERS).json()
    assert stored["status"] == "approved"
    assert stored["decided_by"] == "owner_other"
    assert stored["decision_reason"] == "Reviewed by a second owner"

    with repository_factory(tenant_context(TENANT_A, WORKSPACE_A)) as repository:
        records = {record.action: record for record in repository.list_audit_records(tenant_context())}
    assert {"approval_policy.updated", "approval.self_approval_denied", "approval.approved"} <= set(records)
    assert records["approval.approved"].metadata["reason"] == "Reviewed by a second owner"
    assert records["approval.self_approval_denied"].actor == HEADERS["x-user-id"]


def test_policy_rows_are_isolated_by_workspace(
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
    app_session: Callable[..., Iterator[Session]],
    seed_scopes: None,
) -> None:
    context_a = tenant_context(TENANT_A, WORKSPACE_A)
    with repository_factory(context_a, commit=True) as repository:
        saved = repository.save_approval_policy(
            WorkspaceApprovalPolicy(two_person_rule=True, medium_risk_requires_approval=True), context_a
        )
        assert saved.two_person_rule and saved.medium_risk_requires_approval
        assert saved.updated_by == context_a.user_id

    with repository_factory(context_a) as repository:
        assert repository.get_approval_policy(context_a).two_person_rule is True

    for tenant_id, workspace_id in ((TENANT_A, WORKSPACE_A2), (TENANT_B, WORKSPACE_B)):
        other = tenant_context(tenant_id, workspace_id)
        with repository_factory(other) as repository:
            assert repository.get_approval_policy(other) == WorkspaceApprovalPolicy()
        with app_session(other) as session:
            assert session.execute(text("select count(*) from workspace_approval_policies")).scalar_one() == 0
            assert session.execute(text("update workspace_approval_policies set two_person_rule = false")).rowcount == 0

    with pytest.raises(DBAPIError, match="row-level security"):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A2)) as session:
            session.execute(
                text(
                    "insert into workspace_approval_policies (tenant_id, workspace_id, updated_by) "
                    "values (:t, :w, 'mallory')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )


def test_policy_update_overwrites_the_single_row(
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
    app_session: Callable[..., Iterator[Session]],
    seed_scopes: None,
) -> None:
    context = tenant_context(TENANT_A, WORKSPACE_A)
    with repository_factory(context, commit=True) as repository:
        repository.save_approval_policy(WorkspaceApprovalPolicy(two_person_rule=True), context)
    with repository_factory(context, commit=True) as repository:
        repository.save_approval_policy(WorkspaceApprovalPolicy(two_person_rule=False), context)
    with app_session(context) as session:
        rows = session.execute(text("select two_person_rule from workspace_approval_policies")).all()
    assert [row[0] for row in rows] == [False]
