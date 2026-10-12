"""Setup plans in PostgreSQL (migration 0052_setup_plans), as the non-owner app role.

The whole pilot is applied through the API against PostgreSQL stores; the ledger is read
back without any predicate (RLS alone decides), a second apply creates nothing, another
tenant sees nothing, and a second workspace of the same tenant sees the tenant-level rows.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import automation as automation_module
from anum_api.agent_profiles import agent_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database

PLAN = "shahzeb-business-group"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, roles: str = "owner,org_admin") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": "user_test", "x-user-roles": roles}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(automation_module, "_postgres_engine", None)
    agent_store.clear()
    yield TestClient(app)
    agent_store.clear()


def _apply(client: TestClient, hdrs: dict[str, str] | None = None, phase: str = "pilot") -> dict:
    response = client.post(f"/api/v1/setup-plans/{PLAN}/apply", headers=hdrs or headers(), json={"phase": phase, "plan_version": 1})
    assert response.status_code == 200, response.text
    return response.json()


def _count(app_session: Callable[..., Iterator[Session]], sql: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        return int(session.execute(text(sql)).scalar_one())


def test_pilot_applies_through_postgres_and_reapplying_creates_nothing(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    first = _apply(client)
    failed = [(i["key"], i["error"]) for i in first["items"] if i["outcome"] == "failed"]
    assert not failed, failed
    assert first["outcomes"]["created"] > 100

    agent_store.clear()  # restart: read back from PostgreSQL
    agents = client.get("/api/v1/agents?limit=100", headers=headers()).json()["items"]
    assert len(agents) == 12 and sum(a["status"] == "active" for a in agents) == 3
    workflows = client.get("/api/v1/automation/workflows?limit=200", headers=headers()).json()
    assert len(workflows) == 18 and {w["status"] for w in workflows} == {"disabled"}

    second = _apply(client)
    assert not second["outcomes"].get("created") and not second["outcomes"].get("failed")
    assert _count(app_session, "select count(*) from agent_profiles") == 12

    # RLS alone, no predicate: workspace rows here plus the tenant-level rows.
    total = _count(app_session, "select count(*) from setup_plan_items")
    tenant_level = _count(app_session, "select count(*) from setup_plan_items where workspace_id = ''")
    assert total >= 100
    assert tenant_level == 21 + 24  # memory rules, 4 approval rules, 6 packs, 10 role templates + published skill versions
    assert _count(app_session, "select count(*) from setup_plan_items where state = 'claimed'") == 0

    # Another workspace of the tenant sees only the tenant-level rows; another tenant sees none.
    assert _count(app_session, "select count(*) from setup_plan_items", TENANT_A, WORKSPACE_A2) == tenant_level
    assert _count(app_session, "select count(*) from setup_plan_items", TENANT_B, WORKSPACE_B) == 0
    other = client.get(f"/api/v1/setup-plans/{PLAN}/preview", headers=headers(TENANT_B, WORKSPACE_B)).json()
    assert other["plan"]["applied_items"] == 0
    there = client.get(f"/api/v1/setup-plans/{PLAN}/preview", headers=headers(TENANT_A, WORKSPACE_A2)).json()
    governance = [i for i in there["items"] if i["section"] == "governance" and i["action"] != "builtin"]
    assert governance and {i["action"] for i in governance} == {"present"}

    # The audit record is in PostgreSQL.
    assert _count(app_session, "select count(*) from audit_records where action = 'setup_plan.applied'") == 2


def test_rls_refuses_ledger_rows_for_another_scope(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> None:
    row = (
        "insert into setup_plan_items (tenant_id, workspace_id, plan_id, item_key, kind, state, origin, details, plan_version, "
        "applied_by, created_at, updated_at) values (:tenant, :workspace, 'p', 'k', 'agent', 'applied', 'created', '{}'::jsonb, 1, 'u', now(), now())"
    )
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(row), {"tenant": TENANT_B, "workspace": WORKSPACE_B})
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(row), {"tenant": TENANT_A, "workspace": WORKSPACE_A2})
    with app_session(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as session:
        session.execute(text(row), {"tenant": TENANT_A, "workspace": ""})
    assert _count(app_session, "select count(*) from setup_plan_items", TENANT_A, WORKSPACE_A2) == 1
    assert _count(app_session, "select count(*) from setup_plan_items", TENANT_B, WORKSPACE_B) == 0


def test_claims_in_postgres_refuse_a_fresh_claim_and_retake_a_stale_one(client: TestClient) -> None:
    from datetime import timedelta

    from anum_api.schemas import TenantContext, utc_now
    from anum_api.setup_plan_ledger import LedgerEntry, LedgerState, open_setup_plan_ledger

    context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test", roles=["owner"])

    def claim(key: str, minutes_old: int) -> LedgerEntry:
        when = utc_now() - timedelta(minutes=minutes_old)
        return LedgerEntry(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, plan_id=PLAN, item_key=key, kind="agent",
                           state=LedgerState.CLAIMED, applied_by="user_test", created_at=when, updated_at=when)

    with open_setup_plan_ledger(context) as ledger:
        assert ledger.claim(claim("fresh", 0)) is True
        assert ledger.claim(claim("stale", 30)) is True
    with open_setup_plan_ledger(context) as ledger:
        assert ledger.claim(claim("fresh", 0)) is False
        assert ledger.claim(claim("stale", 0)) is True
        ledger.release(claim("fresh", 0))
    with open_setup_plan_ledger(context) as ledger:
        keys = {entry.item_key for entry in ledger.entries(context, PLAN)}
    assert keys == {"stale"}
    with pytest.raises(PermissionError):
        with open_setup_plan_ledger(context) as ledger:
            ledger.save(claim("x", 0).model_copy(update={"workspace_id": WORKSPACE_A2}))


def test_pilot_checks_persist_per_tenant_with_their_audit(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    put = client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC05", headers=headers(), json={"status": "pass", "evidence": "approval_1"})
    assert put.status_code == 200, put.text
    assert put.json()["passed"] == 1
    again = client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC05", headers=headers(TENANT_A, WORKSPACE_A2), json={"status": "fail"})
    assert again.json()["failed"] == 1 and again.json()["passed"] == 0
    assert _count(app_session, "select count(*) from setup_plan_checks") == 1
    assert _count(app_session, "select count(*) from setup_plan_checks", TENANT_B, WORKSPACE_B) == 0
    other = client.get(f"/api/v1/setup-plans/{PLAN}/checks", headers=headers(TENANT_B, WORKSPACE_B)).json()
    assert other["passed"] == 0 and other["failed"] == 0
    assert _count(app_session, "select count(*) from audit_records where action = 'setup_plan.check_recorded'") == 1
    assert _count(app_session, "select count(*) from audit_records where action = 'setup_plan.check_recorded'", TENANT_A, WORKSPACE_A2) == 1
