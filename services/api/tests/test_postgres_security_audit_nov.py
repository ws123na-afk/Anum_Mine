"""The November 2026 security audit's open items in PostgreSQL, as the non-owner app role.

* The emergency stop reaches another person's private task (hidden from the person who pulls it
  by the 0163 row policies) by acting as that person, and reports a count only.
* An approved call runs as the person who asked: their membership is read through the
  approver's session (``requester_identity``) and the tool sees the requester's identity.
Every row is SYNTHETIC. Memory-backend twins: ``test_security_audit_nov_open_items.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FIXED_NOW, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures
from test_postgres_private_tasks import as_user  # noqa: F401 - pytest fixture

from anum_api import main
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from anum_api.email_api import _pause_for_approval
from anum_api.schemas import RiskLevel, Task, TaskStatus, TenantContext

pytestmark = pytest.mark.database


def _headers(user: str, role: str) -> dict[str, str]:
    return {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": user, "x-user-roles": role}


def _members(database_engine: Engine, *people: tuple[str, str]) -> None:
    with database_engine.begin() as connection:
        for user, role in people:
            connection.execute(text(
                "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values (:u, :t, :w, :r, true) "
                "on conflict do nothing"), {"u": user, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def test_the_stop_cancels_another_persons_private_task_in_sql(
    client: TestClient, database_engine: Engine, as_user: Any,  # noqa: F811
) -> None:
    _members(database_engine, ("alice_owner", "owner"), ("mona_member", "member"))
    with as_user("mona_member") as (_, repository):
        repository.create_task(Task(
            id="task_mona_secret", title="Salary negotiation notes", prompt="private", status=TaskStatus.RUNNING,
            tenant_id=TENANT_A, workspace_id=WORKSPACE_A, created_at=FIXED_NOW, updated_at=FIXED_NOW,
            created_by="mona_member", private_to="mona_member", private_kind="data",
        ))
    alice = _headers("alice_owner", "owner")
    assert client.get("/api/v1/tasks/task_mona_secret", headers=alice).status_code == 404
    try:
        engaged = client.put("/api/v1/agent-controls/emergency-stop", headers=alice, json={"engaged": True, "reason": "Stop"})
        assert engaged.status_code == 200, engaged.text
        assert engaged.json()["cancelled_tasks"] == 1
        assert "Salary negotiation" not in engaged.text and "task_mona_secret" not in engaged.text
        with database_engine.connect() as connection:
            assert connection.execute(text("select status from tasks where id = 'task_mona_secret'")).scalar_one() == "cancelled"
            audit = connection.execute(text(
                "select metadata from audit_records where action = 'agent_controls.emergency_stop.cancelled_tasks'")).scalar_one()
        assert audit["private_tasks_of_others"] == 1 and "task_mona_secret" not in str(audit)
        assert client.get("/api/v1/tasks/task_mona_secret", headers=_headers("mona_member", "member")).json()["status"] == "cancelled"
    finally:
        org_admin = _headers("alice_owner", "owner,org_admin")
        client.put("/api/v1/agent-controls/emergency-stop", headers=org_admin, json={"engaged": False, "reason": "Done"})


def test_an_approved_call_runs_as_the_requester_in_sql(
    client: TestClient, database_engine: Engine, as_user: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    _members(database_engine, ("alice_owner", "owner"), ("mona_member", "member"))
    seen: list[tuple[str, list[str]]] = []

    async def probe(call: ToolCall, context: TenantContext) -> ToolResult:
        seen.append((context.user_id, list(context.roles)))
        return ToolResult(status="succeeded", summary="done", output={})

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="probe.act", description="A high-risk probe.", risk_level=RiskLevel.HIGH,
                                     required_roles=frozenset({"owner", "member"}), category="general", action_kind="other"), probe)
    monkeypatch.setattr(main, "tool_registry", registry)
    mona = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="mona_member", roles=["member"])
    with as_user("mona_member") as (_, repository):
        _, approval = _pause_for_approval(repository, mona, ToolCall(name="probe.act", arguments={}), "Probe", "Probe it.", registry=registry)
    decided = client.post(f"/api/v1/approvals/{approval.id}/approve", headers=_headers("alice_owner", "owner"),
                          json={"payload_hash": approval.payload_hash})
    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["status"] == "completed"
    assert seen == [("mona_member", ["member"])]
