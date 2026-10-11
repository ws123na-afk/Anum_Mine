"""Autonomy policy in PostgreSQL (migration 0031_autonomy, docs/autonomy.md).

The API runs as the non-owner ``anum_test_app`` role through the real repository, so the
forced RLS policies decide what each workspace and tenant can see and write.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api import main
from anum_api.agent_planning import AgentPlanner
from anum_api.agent_tools import ToolCall
from anum_api.autonomy import (
    ActionFilter,
    OrganizationGuardrails,
    WorkspaceAutonomySettings,
    autonomy_memory_store,
)
from anum_api.db.autonomy_repository import SqlAlchemyAutonomyStore
from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import utc_now

from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from test_autonomy import UNDONE, _registry
from test_postgres_runtime import HEADERS, postgres_client  # noqa: F401 (fixture)

pytestmark = pytest.mark.database

MEMBER = {**HEADERS, "x-user-id": "member_x", "x-user-roles": "member"}
PLANNED: dict[str, ToolCall | None] = {"call": None}


@pytest.fixture(autouse=True)
def _tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    registry = _registry()
    monkeypatch.setattr(main, "tool_registry", registry)
    monkeypatch.setattr(main.app.state, "tool_registry", registry)
    original = AgentPlanner._select_tool

    def select(self, prompt, skills, response):  # type: ignore[no-untyped-def]
        return PLANNED["call"] or original(self, prompt, skills, response)

    monkeypatch.setattr(AgentPlanner, "_select_tool", select)
    UNDONE.clear()
    autonomy_memory_store.clear()
    yield
    PLANNED["call"] = None


def _run(client: TestClient, call: ToolCall, who: dict[str, str] = HEADERS) -> dict:
    PLANNED["call"] = call
    task = client.post("/api/v1/tasks", headers=who, json={"title": "Autonomy", "prompt": "Do it"}).json()
    response = client.post(f"/api/v1/tasks/{task['id']}/run", headers=who)
    assert response.status_code == 200, response.text
    return response.json()


def _approve(client: TestClient, body: dict) -> dict:
    approval = body["approval"]
    response = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=HEADERS, json={"payload_hash": approval["payload_hash"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_whole_loop_persists_in_postgresql(
    postgres_client: TestClient,  # noqa: F811
    app_session: Callable[..., Iterator[Session]],
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    client = postgres_client
    assert autonomy_memory_store.settings == {}
    saved = client.put("/api/v1/autonomy/settings", headers=HEADERS, json={"approved_templates": ["order_status"], "recipients_threshold": 3})
    assert saved.status_code == 200, saved.text
    assert client.get("/api/v1/autonomy/settings", headers=HEADERS).json()["settings"]["recipients_threshold"] == 3
    assert autonomy_memory_store.settings == {}  # nothing went to the in-process store

    send = ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "order_status"})
    _approve(client, _run(client, send))  # first contact asks
    assert _run(client, send)["approval"] is None  # now a known recipient

    archive = _run(client, ToolCall(name="email.archive", arguments={"message_id": "m1"}))
    assert archive["approval"] is None
    page = client.get("/api/v1/autonomy/actions?limit=1", headers=HEADERS).json()
    item = page["items"][0]
    assert item["tool"] == "email.archive" and item["undo_available"] and page["next_cursor"]
    second = client.get(f"/api/v1/autonomy/actions?limit=5&cursor={page['next_cursor']}", headers=HEADERS).json()
    assert [i["tool"] for i in second["items"]] == ["whatsapp.send_template"]
    assert client.get("/api/v1/autonomy/actions?q=ARCHIVED", headers=HEADERS).json()["items"][0]["id"] == item["id"]
    undone = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=HEADERS, json={"undo_token": item["undo_token"]})
    assert undone.status_code == 200, undone.text
    assert UNDONE == [{"message_id": "m1"}]
    assert client.get("/api/v1/autonomy/actions?status=undone", headers=HEADERS).json()["items"][0]["id"] == item["id"]

    tightened = client.post(f"/api/v1/autonomy/actions/{item['id']}/tighten", headers=MEMBER)
    assert tightened.status_code == 201
    assert _run(client, ToolCall(name="email.archive", arguments={"message_id": "m2"}))["approval"] is not None

    for _ in range(3):
        _approve(client, _run(client, ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "eta"})))
    (proposal,) = client.get("/api/v1/autonomy/proposals", headers=HEADERS).json()
    assert proposal["approvals"] == 3
    accepted = client.post(f"/api/v1/autonomy/proposals/{proposal['id']}/accept", headers=HEADERS)
    assert accepted.status_code == 200, accepted.text

    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        hashes = session.execute(text("select recipient_hash from autonomy_recipients")).scalars().all()
        assert hashes and all(len(value) == 64 and "966" not in value for value in hashes)
        rows = dict(session.execute(text("select pattern_key, status from autonomy_proposals")).all())
        # The first-contact approval of order_status counted once; eta was proposed and accepted.
        assert rows == {"whatsapp.send_template|template=order_status": "collecting", "whatsapp.send_template|template=eta": "accepted"}
    with repository_factory(tenant_context(TENANT_A, WORKSPACE_A)) as repository:
        actions = {record.action for record in repository.list_audit_records(tenant_context())}
    assert {
        "autonomy.settings_updated", "autonomy.action_taken", "autonomy.action_undone", "autonomy.rule_created",
        "autonomy.proposal_created", "autonomy.proposal_accepted",
    } <= actions


def test_rows_are_isolated_by_workspace_and_tenant(
    app_session: Callable[..., Iterator[Session]],
    seed_scopes: None,
) -> None:
    now = utc_now()
    a, a2, b = tenant_context(TENANT_A, WORKSPACE_A), tenant_context(TENANT_A, WORKSPACE_A2), tenant_context(TENANT_B, WORKSPACE_B)
    with app_session(a, commit=True) as session:
        store = SqlAlchemyAutonomyStore(session)
        store.save_settings(a, WorkspaceAutonomySettings(recipients_threshold=1))
        store.save_guardrails(a, OrganizationGuardrails(spending_limit=500))
        store.remember_recipients(a, "email", ["0" * 64], now)
        from anum_api.autonomy import AutonomousAction, ActionKind, AutonomyLevel, ToolCategory

        store.add_action(
            AutonomousAction(
                id="auto_a", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, task_id="t", run_id="r", tool="email.archive",
                category=ToolCategory.EMAIL, action_kind=ActionKind.ARCHIVE, level=AutonomyLevel.ACT_AND_NOTIFY,
                pattern_key="email.archive", summary="Archived", requested_by="user_test", created_at=now,
            )
        )
        assert store.count_actions_since(a, now - timedelta(minutes=1), category="email", tool=None) == 1
    with app_session(a2) as session:
        store = SqlAlchemyAutonomyStore(session)
        assert store.get_settings(a2) is None
        assert store.get_guardrails(a2).spending_limit == 500  # tenant-level: every workspace of the tenant
        assert store.list_actions(a2, ActionFilter(), limit=10) == []
        assert store.known_recipients(a2, "email", ["0" * 64]) == set()
        for table in ("workspace_autonomy_settings", "autonomy_actions", "autonomy_recipients"):
            assert session.execute(text(f"select count(*) from {table}")).scalar_one() == 0  # nosec B608 - fixed names
    with app_session(b) as session:
        store = SqlAlchemyAutonomyStore(session)
        assert store.get_guardrails(b) is None and store.get_settings(b) is None
        assert session.execute(text("select count(*) from autonomy_guardrails")).scalar_one() == 0
        assert session.execute(text("update autonomy_actions set status = 'undone'")).rowcount == 0
    with app_session(b) as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "insert into autonomy_rules (tenant_id, workspace_id, id, pattern_key, tool, category, action_kind, level, source, created_by) "
                    "values (:t, :w, 'r1', 'x', 'x', 'email', 'send', 'act_silently', 'manual', 'u')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
    with app_session(a) as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "insert into autonomy_rules (tenant_id, workspace_id, id, pattern_key, tool, category, action_kind, level, source, created_by) "
                    "values (:t, :w, 'r2', 'x', 'x', 'email', 'send', 'whenever', 'manual', 'u')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
