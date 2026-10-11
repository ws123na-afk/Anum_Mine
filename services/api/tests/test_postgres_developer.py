"""Developer-mode requests in PostgreSQL (migrations 0022_developer_requests, 0023_developer_one_active).

The API runs as the non-owner ``anum_test_app`` role against a fake GitHub. Requests are
scoped to their tenant and workspace by forced RLS; "restart" clears the in-memory store
so everything is read back from PostgreSQL.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from conftest import (
    APP_ROLE,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    grant_app_role,
    run_migration,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker
from test_developer import PAT, REPO, FakeGitHub, SlowDispatch, create_concurrently

from anum_api import developer
from anum_api.db import session as db_session
from anum_api.developer import (
    DeveloperAdmissionError,
    DeveloperRequest,
    DevRequestSource,
    DevRequestStatus,
    developer_settings,
    developer_store,
    open_developer_store,
)
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "owner_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


A = headers()


@pytest.fixture
def github(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    fake = FakeGitHub()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(PAT))
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT_A)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    developer_store.clear()
    yield fake
    developer_store.clear()


@pytest.fixture
def client(github: FakeGitHub) -> TestClient:
    return TestClient(app)


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())


def test_requests_persist_and_merge_through_an_approval(client: TestClient, github: FakeGitHub, database_engine: Engine) -> None:
    created = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"})
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert _count(database_engine, "developer_requests") == 1
    developer_store.clear()  # restart: read back from PostgreSQL

    github.start_run(request_id, status="completed", conclusion="success")
    github.open_pull(request_id)
    github.set_ci("a" * 40, "success")
    listed = client.get("/api/v1/developer/requests", headers=A).json()
    assert [(item["id"], item["status"], item["pr_number"]) for item in listed] == [(request_id, "waiting_approval", 7)]
    assert listed[0]["checks"]["passed"] == 1

    approval = client.post(f"/api/v1/developer/requests/{request_id}/merge-approval", headers=A).json()["approval"]
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert len(github.merges) == 1
    merged = client.get(f"/api/v1/developer/requests/{request_id}", headers=A).json()
    assert merged["status"] == "merged" and merged["merge_commit_sha"] == "m" * 40
    with database_engine.connect() as connection:
        actions = set(connection.execute(text("select action from audit_records where correlation_id = :id"), {"id": request_id}).scalars())
    assert {"developer.request.created", "developer.merge.requested", "developer.merge.completed"} <= actions


def test_requests_are_isolated_by_tenant_and_workspace(client: TestClient, github: FakeGitHub) -> None:
    request_id = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"}).json()["id"]
    for other in (headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get("/api/v1/developer/requests", headers=other).json() == []
        assert client.get(f"/api/v1/developer/requests/{request_id}", headers=other).status_code == 404


def test_forced_rls_refuses_rows_outside_the_session_scope(github: FakeGitHub, database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "insert into developer_requests (id, tenant_id, workspace_id, created_by, text, source, status, branch) "
                    "values ('dev_x', :t, :w, 'u', 'text', 'text', 'queued', 'anum-dev/dev_x')"
                ),
                {"t": TENANT_B, "w": WORKSPACE_B},
            )
        transaction.rollback()



CONTEXT_A = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_test", roles=["owner"])


def _request(request_id: str, context: TenantContext = CONTEXT_A) -> DeveloperRequest:
    now = utc_now()
    return DeveloperRequest(
        id=request_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_by=context.user_id,
        text="Add a dark/light toggle to settings", source=DevRequestSource.TEXT, status=DevRequestStatus.QUEUED,
        branch=f"anum-dev/{request_id}", created_at=now, updated_at=now,
    )


def _statuses(database_engine: Engine) -> list[str]:
    with database_engine.connect() as connection:
        return sorted(connection.execute(text("select status from developer_requests")).scalars())


def test_concurrent_creates_admit_one_request_and_dispatch_once(
    github: FakeGitHub, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(developer, "github_transport", SlowDispatch(github))
    responses = asyncio.run(
        create_concurrently(app, A, ["Add a dark/light toggle to settings", "Add a compact table density option"])
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(github.dispatches) == 1
    [created] = [response.json() for response in responses if response.status_code == 201]
    assert github.dispatches[0]["client_payload"]["request_id"] == created["id"]
    assert _count(database_engine, "developer_requests") == 1
    # Another workspace of the same tenant is not blocked by this one.
    other = asyncio.run(create_concurrently(app, headers(workspace_id=WORKSPACE_A2), ["Add a compact table density option"]))
    assert [response.status_code for response in other] == [201]


def test_admission_waits_for_a_concurrent_admission_to_commit(github: FakeGitHub, database_engine: Engine) -> None:
    """Two sessions at once: the second blocks on the scope's advisory lock, then sees the first."""
    first_admitted = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    outcomes: dict[str, str] = {}
    since = utc_now() - timedelta(days=1)

    def first() -> None:
        with open_developer_store(CONTEXT_A) as store:
            store.admit(_request("dev_first"), since=since, limit=10)
            first_admitted.set()
            release_first.wait(10)  # commit only when the test says so
        outcomes["first"] = "admitted"

    def second() -> None:
        try:
            with open_developer_store(CONTEXT_A) as store:
                store.admit(_request("dev_second"), since=since, limit=10)
            outcomes["second"] = "admitted"
        except DeveloperAdmissionError as exc:
            outcomes["second"] = exc.reason
        finally:
            second_done.set()

    one = threading.Thread(target=first)
    one.start()
    assert first_admitted.wait(10)
    two = threading.Thread(target=second)
    two.start()
    assert not second_done.wait(0.5), "the second admission must wait for the first to commit"
    release_first.set()
    one.join(10)
    two.join(10)
    assert outcomes == {"first": "admitted", "second": "active"}
    assert _statuses(database_engine) == ["queued"]


def test_admission_counts_the_daily_limit_inside_the_lock(github: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    since = utc_now() - timedelta(days=1)
    with open_developer_store(CONTEXT_A) as store:
        store.admit(_request("dev_one").model_copy(update={"status": DevRequestStatus.CANCELLED}), since=since, limit=1)
    with pytest.raises(DeveloperAdmissionError) as refused:
        with open_developer_store(CONTEXT_A) as store:
            store.admit(_request("dev_two"), since=since, limit=1)
    assert refused.value.reason == "daily_limit"


def test_the_database_refuses_a_second_active_request_without_the_lock(github: FakeGitHub, database_engine: Engine) -> None:
    """uq_developer_requests_one_active holds even for a writer that skips admission."""
    with open_developer_store(CONTEXT_A) as store:
        store.add(_request("dev_one"))
    with pytest.raises(IntegrityError) as refused:
        with open_developer_store(CONTEXT_A) as store:
            store.add(_request("dev_two").model_copy(update={"status": DevRequestStatus.CODING}))
    assert "uq_developer_requests_one_active" in str(refused.value.orig)
    with open_developer_store(CONTEXT_A) as store:
        store.add(_request("dev_three").model_copy(update={"status": DevRequestStatus.FAILED}))
    other_workspace = CONTEXT_A.model_copy(update={"workspace_id": WORKSPACE_A2})
    with open_developer_store(other_workspace) as store:
        store.add(_request("dev_four", other_workspace))
    assert _statuses(database_engine) == ["failed", "queued", "queued"]


def test_dispatch_failure_marks_the_stored_request_failed(client: TestClient, github: FakeGitHub, database_engine: Engine) -> None:
    github.fail_dispatch = True
    assert client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"}).status_code == 502
    developer_store.clear()
    [failed] = client.get("/api/v1/developer/requests", headers=A).json()
    assert failed["status"] == "failed" and failed["failure_reason"].startswith("Not sent to GitHub:")
    github.fail_dispatch = False
    retried = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"})
    assert retried.status_code == 201, retried.text
    assert _statuses(database_engine) == ["failed", "queued"]


def test_one_active_migration_keeps_the_newest_active_request_per_workspace(
    database_engine: Engine, test_database_url: str, seed_scopes: None
) -> None:
    """Rows from the old race: 0023 fails all but the newest active request, then indexes."""
    rows = (
        ("dev_old", WORKSPACE_A, "queued", "2026-10-01T10:00:00Z"),
        ("dev_new", WORKSPACE_A, "coding", "2026-10-01T10:00:01Z"),
        ("dev_done", WORKSPACE_A, "merged", "2026-09-30T10:00:00Z"),
        ("dev_a2", WORKSPACE_A2, "queued", "2026-10-01T09:00:00Z"),
    )
    try:
        run_migration(test_database_url, "downgrade", "0022_developer_requests")
        with database_engine.begin() as connection:
            for request_id, workspace_id, status, created_at in rows:
                connection.execute(
                    text(
                        "insert into developer_requests "
                        "(id, tenant_id, workspace_id, created_by, text, source, status, branch, created_at) "
                        "values (:id, :tenant, :workspace, 'u', 'text', 'text', :status, :branch, :created_at)"
                    ),
                    {
                        "id": request_id, "tenant": TENANT_A, "workspace": workspace_id, "status": status,
                        "branch": f"anum-dev/{request_id}", "created_at": created_at,
                    },
                )
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)

    with database_engine.connect() as connection:
        found = dict(connection.execute(text("select id, status from developer_requests")).tuples().all())
        reason = connection.execute(text("select failure_reason from developer_requests where id = 'dev_old'")).scalar_one()
        index = connection.execute(
            text(
                "select indisunique and indisvalid, pg_get_expr(indpred, indrelid) from pg_index "
                "where indexrelid = to_regclass('uq_developer_requests_one_active')"
            )
        ).one()
        forced = connection.execute(
            text("select relforcerowsecurity from pg_class where relname = 'developer_requests'")
        ).scalar_one()
    assert found == {"dev_old": "failed", "dev_new": "coding", "dev_done": "merged", "dev_a2": "queued"}
    assert reason and "already queued or coding" in reason
    assert index[0] is True and "queued" in index[1] and "coding" in index[1]
    assert forced is True
