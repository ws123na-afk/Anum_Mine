"""Developer mode in PostgreSQL (migrations 0022, 0023 and 0032_developer_projects).

The API runs as the non-owner ``anum_test_app`` role against a fake GitHub. Projects and
requests are scoped to their tenant and workspace by forced RLS; "restart" clears the
in-memory stores so everything is read back from PostgreSQL.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

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
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker
from test_developer import OTHER_PAT, OTHER_REPO, PAT, REPO, FakeGitHub, GitHubHub, SlowDispatch, create_concurrently

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
from anum_api.developer_projects import project_store
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
    other = FakeGitHub(OTHER_REPO, OTHER_PAT)
    fake.hub = GitHubHub(fake, other)  # type: ignore[attr-defined]
    fake.other = other  # type: ignore[attr-defined]
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(fake.hub))  # type: ignore[attr-defined]
    monkeypatch.setattr(developer_settings, "github_repo", "")
    monkeypatch.setattr(developer_settings, "github_token", None)
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT_A)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    developer_store.clear()
    project_store.clear()
    yield fake
    developer_store.clear()
    project_store.clear()


@pytest.fixture
def client(github: FakeGitHub) -> TestClient:
    return TestClient(app)


def add_project(client: TestClient, hdrs: dict[str, str] = A, name: str = "ANUM", repo: str = REPO, token: str = PAT, **extra: Any) -> dict[str, Any]:
    owner, repo_name = repo.split("/")
    response = client.post(
        "/api/v1/developer/projects", headers=hdrs, json={"name": name, "github_owner": owner, "github_repo": repo_name, "token": token, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def project(client: TestClient) -> dict[str, Any]:
    return add_project(client)


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())


def test_requests_persist_and_merge_through_an_approval(
    client: TestClient, github: FakeGitHub, project: dict[str, Any], database_engine: Engine
) -> None:
    created = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"})
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert _count(database_engine, "developer_requests") == 1
    developer_store.clear()  # restart: read back from PostgreSQL
    project_store.clear()

    github.start_run(request_id, status="completed", conclusion="success")
    github.open_pull(request_id)
    github.set_ci("a" * 40, "success")
    listed = client.get("/api/v1/developer/requests", headers=A).json()
    assert [(item["id"], item["status"], item["pr_number"]) for item in listed] == [(request_id, "waiting_approval", 7)]
    assert (listed[0]["project_id"], listed[0]["project_name"], listed[0]["repository"]) == (project["id"], "ANUM", REPO)
    assert listed[0]["checks"]["passed"] == 1

    approval = client.post(f"/api/v1/developer/requests/{request_id}/merge-approval", headers=A).json()["approval"]
    assert approval["arguments"]["repository"] == REPO
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert len(github.merges) == 1
    merged = client.get(f"/api/v1/developer/requests/{request_id}", headers=A).json()
    assert merged["status"] == "merged" and merged["merge_commit_sha"] == "m" * 40
    with database_engine.connect() as connection:
        actions = set(connection.execute(text("select action from audit_records where correlation_id = :id"), {"id": request_id}).scalars())
    assert {"developer.request.created", "developer.merge.requested", "developer.merge.completed"} <= actions


def test_projects_persist_encrypted_and_isolated(client: TestClient, github: FakeGitHub, database_engine: Engine) -> None:
    created = add_project(client, description="The ANUM platform", protected_paths=["docs/legal/"])
    project_store.clear()
    [listed] = client.get("/api/v1/developer/projects", headers=A).json()
    assert (listed["id"], listed["protected_paths"], listed["token_configured"]) == (created["id"], ["docs/legal/"], True)
    with database_engine.connect() as connection:
        ciphertext = connection.execute(text("select token_ciphertext from developer_projects")).scalar_one()
    assert ciphertext and PAT not in ciphertext
    for other in (headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get(f"/api/v1/developer/projects/{created['id']}", headers=other).status_code in {403, 404}
    assert client.get("/api/v1/developer/projects", headers=headers(workspace_id=WORKSPACE_A2)).json() == []
    # Names and repositories are unique per workspace (case-insensitive indexes), not across workspaces.
    duplicate = client.post(
        "/api/v1/developer/projects", headers=A, json={"name": "anum", "github_owner": "acme", "github_repo": "else", "token": PAT}
    )
    assert duplicate.status_code == 409
    add_project(client, headers(workspace_id=WORKSPACE_A2))
    tested = client.post(f"/api/v1/developer/projects/{created['id']}/test", headers=A).json()
    project_store.clear()
    again = client.get(f"/api/v1/developer/projects/{created['id']}", headers=A).json()
    assert again["last_check"]["ok"] is True and again["last_check"]["message"] == tested["last_check"]["message"]
    assert client.delete(f"/api/v1/developer/projects/{created['id']}", headers=A).status_code == 204
    assert _count(database_engine, "developer_projects") == 1


def test_requests_are_isolated_by_tenant_and_workspace(client: TestClient, github: FakeGitHub, project: dict[str, Any]) -> None:
    request_id = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"}).json()["id"]
    for other in (headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get("/api/v1/developer/requests", headers=other).json() == []
        assert client.get(f"/api/v1/developer/requests/{request_id}", headers=other).status_code == 404


@pytest.mark.parametrize(
    "statement",
    [
        "insert into developer_requests (id, tenant_id, workspace_id, created_by, text, source, status, branch) "
        "values ('dev_x', :t, :w, 'u', 'text', 'text', 'queued', 'anum-dev/dev_x')",
        "insert into developer_projects (id, tenant_id, workspace_id, name, github_owner, github_repo, created_by) "
        "values ('devproj_x', :t, :w, 'x', 'acme', 'x', 'u')",
    ],
)
def test_forced_rls_refuses_rows_outside_the_session_scope(github: FakeGitHub, database_engine: Engine, statement: str) -> None:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
        with pytest.raises(DBAPIError):
            connection.execute(text(statement), {"t": TENANT_B, "w": WORKSPACE_B})
        transaction.rollback()


CONTEXT_A = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_test", roles=["owner"])


def _request(request_id: str, context: TenantContext = CONTEXT_A, project_id: str | None = "devproj_a") -> DeveloperRequest:
    now = utc_now()
    return DeveloperRequest(
        id=request_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_by=context.user_id,
        text="Add a dark/light toggle to settings", source=DevRequestSource.TEXT, status=DevRequestStatus.QUEUED,
        branch=f"anum-dev/{request_id}", project_id=project_id, created_at=now, updated_at=now,
    )


def _statuses(database_engine: Engine) -> list[str]:
    with database_engine.connect() as connection:
        return sorted(connection.execute(text("select status from developer_requests")).scalars())


def test_concurrent_creates_admit_one_request_per_project_and_dispatch_once(
    client: TestClient, github: FakeGitHub, project: dict[str, Any], database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = add_project(client, name="TyrePulse", repo=OTHER_REPO, token=OTHER_PAT)
    monkeypatch.setattr(developer, "github_transport", SlowDispatch(github.hub))  # type: ignore[attr-defined]
    responses = asyncio.run(
        create_concurrently(app, A, ["Add a dark/light toggle to settings", "Add a compact table density option"], project["id"])
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(github.dispatches) == 1
    [created] = [response.json() for response in responses if response.status_code == 201]
    assert github.dispatches[0]["client_payload"]["request_id"] == created["id"]
    assert _count(database_engine, "developer_requests") == 1
    # Another project of the same workspace is not blocked by this one.
    elsewhere = asyncio.run(create_concurrently(app, A, ["Add a tread depth chart"], other["id"]))
    assert [response.status_code for response in elsewhere] == [201]
    assert len(github.other.dispatches) == 1  # type: ignore[attr-defined]


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


def test_admission_counts_the_daily_limit_across_projects_inside_the_lock(github: FakeGitHub) -> None:
    since = utc_now() - timedelta(days=1)
    with open_developer_store(CONTEXT_A) as store:
        store.admit(_request("dev_one").model_copy(update={"status": DevRequestStatus.CANCELLED}), since=since, limit=1)
    with pytest.raises(DeveloperAdmissionError) as refused:
        with open_developer_store(CONTEXT_A) as store:
            store.admit(_request("dev_two", project_id="devproj_b"), since=since, limit=1)
    assert refused.value.reason == "daily_limit"


def test_the_database_refuses_a_second_active_request_per_project_without_the_lock(
    github: FakeGitHub, database_engine: Engine
) -> None:
    """uq_developer_requests_one_active_project holds even for a writer that skips admission."""
    with open_developer_store(CONTEXT_A) as store:
        store.add(_request("dev_one"))
    with pytest.raises(IntegrityError) as refused:
        with open_developer_store(CONTEXT_A) as store:
            store.add(_request("dev_two").model_copy(update={"status": DevRequestStatus.CODING}))
    assert "uq_developer_requests_one_active_project" in str(refused.value.orig)
    with open_developer_store(CONTEXT_A) as store:
        store.add(_request("dev_three").model_copy(update={"status": DevRequestStatus.FAILED}))
        store.add(_request("dev_four", project_id="devproj_b"))  # another project
        store.add(_request("dev_five", project_id=None))  # the pre-projects repository: one active too
    with pytest.raises(IntegrityError):
        with open_developer_store(CONTEXT_A) as store:
            store.add(_request("dev_six", project_id=None))
    other_workspace = CONTEXT_A.model_copy(update={"workspace_id": WORKSPACE_A2})
    with open_developer_store(other_workspace) as store:
        store.add(_request("dev_seven", other_workspace))
    assert _statuses(database_engine) == ["failed", "queued", "queued", "queued", "queued"]


def test_dispatch_failure_marks_the_stored_request_failed(
    client: TestClient, github: FakeGitHub, project: dict[str, Any], database_engine: Engine
) -> None:
    github.fail_dispatch = True
    assert client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"}).status_code == 502
    developer_store.clear()
    [failed] = client.get("/api/v1/developer/requests", headers=A).json()
    assert failed["status"] == "failed" and failed["failure_reason"].startswith("Not sent to GitHub:")
    github.fail_dispatch = False
    retried = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"})
    assert retried.status_code == 201, retried.text
    assert _statuses(database_engine) == ["failed", "queued"]


def test_project_with_unfinished_requests_is_kept(
    client: TestClient, github: FakeGitHub, project: dict[str, Any]
) -> None:
    created = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"}).json()
    assert client.delete(f"/api/v1/developer/projects/{project['id']}", headers=A).status_code == 409
    client.post(f"/api/v1/developer/requests/{created['id']}/cancel", headers=A)
    assert client.delete(f"/api/v1/developer/projects/{project['id']}", headers=A).status_code == 204
    [kept] = client.get("/api/v1/developer/requests", headers=A).json()
    assert kept["project_name"] == "ANUM" and kept["status"] == "cancelled"


def _insert_old_requests(database_engine: Engine, rows: tuple[tuple[str, str, str, str], ...]) -> None:
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


def _index(database_engine: Engine, name: str) -> Any:
    with database_engine.connect() as connection:
        return connection.execute(
            text(
                "select indisunique and indisvalid, pg_get_expr(indpred, indrelid), pg_get_indexdef(indexrelid) from pg_index "
                "where indexrelid = to_regclass(:name)"
            ),
            {"name": name},
        ).first()


def test_one_active_migration_keeps_the_newest_active_request_per_workspace(
    database_engine: Engine, test_database_url: str, seed_scopes: None
) -> None:
    """Rows from the old race: 0023 fails all but the newest active request; 0031 then indexes per project."""
    rows = (
        ("dev_old", WORKSPACE_A, "queued", "2026-10-01T10:00:00Z"),
        ("dev_new", WORKSPACE_A, "coding", "2026-10-01T10:00:01Z"),
        ("dev_done", WORKSPACE_A, "merged", "2026-09-30T10:00:00Z"),
        ("dev_a2", WORKSPACE_A2, "queued", "2026-10-01T09:00:00Z"),
    )
    try:
        run_migration(test_database_url, "downgrade", "0022_developer_requests")
        _insert_old_requests(database_engine, rows)
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)

    with database_engine.connect() as connection:
        found = dict(connection.execute(text("select id, status from developer_requests")).tuples().all())
        reason = connection.execute(text("select failure_reason from developer_requests where id = 'dev_old'")).scalar_one()
        forced = connection.execute(
            text("select relforcerowsecurity from pg_class where relname = 'developer_requests'")
        ).scalar_one()
    assert found == {"dev_old": "failed", "dev_new": "coding", "dev_done": "merged", "dev_a2": "queued"}
    assert reason and "already queued or coding" in reason
    assert _index(database_engine, "uq_developer_requests_one_active") is None
    index = _index(database_engine, "uq_developer_requests_one_active_project")
    assert index[0] is True and "queued" in index[1] and "coding" in index[1] and "COALESCE(project_id" in index[2]
    assert forced is True


def test_projects_migration_turns_the_configured_repository_into_the_first_project(
    database_engine: Engine, test_database_url: str, seed_scopes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ANUM_DEVELOPER_GITHUB_REPO becomes a project in each workspace of the tenant, and old requests join it."""
    monkeypatch.setenv("ANUM_DEVELOPER_GITHUB_REPO", "acme/anum")
    monkeypatch.setenv("ANUM_DEVELOPER_TENANT_ID", TENANT_A)
    monkeypatch.setenv("ANUM_DEVELOPER_BASE_BRANCH", "develop")
    monkeypatch.delenv("ANUM_DEVELOPER_WORKSPACE_ID", raising=False)
    try:
        run_migration(test_database_url, "downgrade", "0031_automation_policy")
        _insert_old_requests(
            database_engine,
            (("dev_a", WORKSPACE_A, "merged", "2026-10-01T10:00:00Z"), ("dev_a2", WORKSPACE_A2, "queued", "2026-10-01T10:00:00Z")),
        )
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)
    with database_engine.connect() as connection:
        projects = connection.execute(
            text(
                "select tenant_id, workspace_id, name, github_owner, github_repo, default_branch, token_source, "
                "token_ciphertext, created_by, id from developer_projects order by workspace_id"
            )
        ).all()
        requests = dict(connection.execute(text("select id, project_id from developer_requests")).tuples().all())
        forced = connection.execute(text("select relforcerowsecurity from pg_class where relname = 'developer_projects'")).scalar_one()
    assert [tuple(row)[:9] for row in projects] == [
        (TENANT_A, WORKSPACE_A, "anum", "acme", "anum", "develop", "environment", None, "system:migration"),
        (TENANT_A, WORKSPACE_A2, "anum", "acme", "anum", "develop", "environment", None, "system:migration"),
    ]
    assert requests == {"dev_a": projects[0].id, "dev_a2": projects[1].id}
    assert forced is True


def test_projects_migration_honours_the_workspace_setting_and_skips_without_settings(
    database_engine: Engine, test_database_url: str, seed_scopes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANUM_DEVELOPER_GITHUB_REPO", "acme/anum")
    monkeypatch.setenv("ANUM_DEVELOPER_TENANT_ID", TENANT_A)
    monkeypatch.setenv("ANUM_DEVELOPER_WORKSPACE_ID", WORKSPACE_A2)
    try:
        run_migration(test_database_url, "downgrade", "0031_automation_policy")
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)
    with database_engine.connect() as connection:
        assert list(connection.execute(text("select workspace_id from developer_projects")).scalars()) == [WORKSPACE_A2]
    monkeypatch.delenv("ANUM_DEVELOPER_GITHUB_REPO")
    try:
        run_migration(test_database_url, "downgrade", "0031_automation_policy")
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)
    assert _count(database_engine, "developer_projects") == 0
