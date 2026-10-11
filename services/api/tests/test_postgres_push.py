"""Phone push devices and delivery in PostgreSQL (migration 0027_push_devices).

The API runs as the non-owner ``anum_test_app`` role. A device row is private to one user
in one workspace: the forced RLS policy checks ``anum.tenant_id``, ``anum.workspace_id``
and ``anum.user_id``. The dispatcher resolves recipients and reads each recipient's
devices inside that recipient's own RLS context; developer requests in progress are
discovered as ``anum_maintenance``, which sees four columns of unfinished rows only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_developer import PAT, REPO, FakeGitHub
from test_push import SERVICE_ACCOUNT, FakeSender

from anum_api import developer
from anum_api.db import session as db_session
from anum_api.dependencies import event_runtime
from anum_api.developer import developer_settings, developer_store
from anum_api.developer_watch import discover_in_progress, refresh_in_progress
from anum_api.events import CanonicalEventName, create_event
from anum_api.main import app
from anum_api.push import push_dispatcher, push_device_store
from anum_api.push_fcm import SendOutcome
from anum_api.schemas import TenantContext
from anum_api.secret_box import secret_cipher
from anum_api.settings import settings

pytestmark = pytest.mark.database

PHONE_OWNER = "fcm-token-example-owner-phone"
PHONE_OTHER = "fcm-token-example-other-phone"


def headers(user_id: str = "owner_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


def ctx(user_id: str = "owner_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=["owner"])


@pytest.fixture
def sender(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSender]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    fake = FakeSender()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "fcm_service_account_json", SecretStr(SERVICE_ACCOUNT))
    monkeypatch.setattr(push_dispatcher, "_sender_factory", lambda: fake)
    monkeypatch.setattr(push_dispatcher, "_sender", None)
    push_device_store.clear()
    developer_store.clear()
    with push_dispatcher._lock:
        push_dispatcher._queue.clear()
    _members(database_engine, [("owner_test", "owner"), ("owner_two", "owner"), ("member_test", "member")])
    yield fake
    with push_dispatcher._lock:
        push_dispatcher._queue.clear()


@pytest.fixture
def client(sender: FakeSender) -> TestClient:
    return TestClient(app)


def _members(database_engine: Engine, members: list[tuple[str, str]]) -> None:
    with database_engine.begin() as connection:
        for user_id, role in members:
            connection.execute(
                text(
                    "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                    "values (:user_id, :tenant_id, :workspace_id, :role, true)"
                ),
                {"user_id": user_id, "tenant_id": TENANT_A, "workspace_id": WORKSPACE_A, "role": role},
            )


def _register(client: TestClient, user_id: str, token: str, role: str = "owner") -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/me/devices", headers=headers(user_id, role=role), json={"platform": "android", "token": token})
    assert response.status_code in (200, 201), response.text
    return response.json()


def _visible(app_session: Callable[..., Iterator[Session]], context: TenantContext, user_id: str | None) -> int:
    with app_session(context) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text("select count(*) from push_devices")).scalar_one())


def test_devices_persist_encrypted_and_private_to_their_user(
    client: TestClient, database_engine: Engine, app_session: Callable[..., Iterator[Session]]
) -> None:
    device = _register(client, "owner_test", PHONE_OWNER)
    assert _register(client, "owner_test", PHONE_OWNER)["id"] == device["id"]
    _register(client, "owner_two", PHONE_OTHER)
    push_device_store.clear()  # restart: everything below is read back from PostgreSQL

    listed = client.get("/api/v1/me/devices", headers=headers()).json()
    assert [item["id"] for item in listed] == [device["id"]] and listed[0]["token_hint"] == PHONE_OWNER[-4:]
    with database_engine.connect() as connection:
        rows = connection.execute(text("select user_id, token_ciphertext, token_hint from push_devices order by user_id")).all()
    assert [row.user_id for row in rows] == ["owner_test", "owner_two"]
    assert all(PHONE_OWNER not in row.token_ciphertext for row in rows)
    assert secret_cipher().decrypt(rows[0].token_ciphertext) == PHONE_OWNER

    assert _visible(app_session, tenant_context(), "owner_test") == 1
    assert _visible(app_session, tenant_context(), "someone_else") == 0
    assert _visible(app_session, tenant_context(), None) == 0
    assert _visible(app_session, tenant_context(TENANT_B, WORKSPACE_B), "owner_test") == 0

    assert client.delete(f"/api/v1/me/devices/{device['id']}", headers=headers("owner_two")).status_code == 404
    assert client.delete(f"/api/v1/me/devices/{device['id']}", headers=headers()).status_code == 204
    assert client.get("/api/v1/me/devices", headers=headers()).json() == []


def test_forced_rls_refuses_writes_for_another_user(app_session: Callable[..., Iterator[Session]], sender: FakeSender) -> None:
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'owner_test', true)"))
        with pytest.raises(DBAPIError, match="row-level security"):
            session.execute(
                text(
                    "insert into push_devices (id, tenant_id, workspace_id, user_id, platform, token_ciphertext, "
                    "token_sha256, token_hint) values ('device_x', :tenant, :workspace, 'owner_two', 'android', 'c', "
                    "repeat('0', 64), 'abcd')"
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A},
            )


def test_push_preference_round_trips(client: TestClient) -> None:
    saved = client.put("/api/v1/notification-preferences", headers=headers(), json={"push_enabled": False})
    assert saved.status_code == 200 and saved.json()["push_enabled"] is False
    assert client.get("/api/v1/notification-preferences", headers=headers()).json()["push_enabled"] is False
    # Older clients that never send push_enabled leave it on.
    other = client.put("/api/v1/notification-preferences", headers=headers("owner_two"), json={"email_enabled": True})
    assert other.json()["push_enabled"] is True


def test_approval_push_is_resolved_and_delivered_under_rls(client: TestClient, sender: FakeSender, database_engine: Engine) -> None:
    _register(client, "owner_test", PHONE_OWNER)
    _register(client, "owner_two", PHONE_OTHER)
    _register(client, "member_test", "fcm-token-example-member-phone", role="member")
    sender.outcomes[PHONE_OTHER] = SendOutcome.UNREGISTERED
    task = client.post("/api/v1/tasks", headers=headers("member_test", role="member"), json={"title": "T", "prompt": "Summarize notes"})
    assert task.status_code == 201, task.text
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason, created_at) "
                "values ('approval_push', :tenant, :workspace, :task, 'email.send', 'high', 'pending', 'r', now())"
            ),
            {"tenant": TENANT_A, "workspace": WORKSPACE_A, "task": task.json()["id"]},
        )
    event_runtime.after_commit(
        [create_event(CanonicalEventName.APPROVAL_REQUESTED, ctx(), "approval_push", {"task_id": task.json()["id"]}).event]
    )
    report = asyncio.run(push_dispatcher.run_pending())
    assert (report.recipients, report.sent, report.removed) == (2, 1, 1)
    assert sorted(message.token for message in sender.messages) == sorted([PHONE_OWNER, PHONE_OTHER])
    with database_engine.connect() as connection:
        rows = connection.execute(text("select user_id, last_sent_at from push_devices order by user_id")).all()
    assert [(row.user_id, row.last_sent_at is not None) for row in rows] == [("member_test", False), ("owner_test", True)]


def test_maintenance_discovers_unfinished_developer_requests_only(
    client: TestClient, sender: FakeSender, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = FakeGitHub()
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(github))
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(PAT))
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT_A)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    _register(client, "owner_test", PHONE_OWNER)
    owner, repo = REPO.split("/")
    added = client.post(
        "/api/v1/developer/projects",
        headers=headers(),
        json={"name": "ANUM", "github_owner": owner, "github_repo": repo, "token_source": "environment"},
    )
    assert added.status_code == 201, added.text
    created = client.post("/api/v1/developer/requests", headers=headers(), json={"text": "Add a dark mode toggle to settings"})
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    developer_store.clear()
    assert discover_in_progress(TENANT_A) == [(request_id, WORKSPACE_A)]
    assert discover_in_progress(TENANT_B) == []

    with database_engine.connect() as connection:
        for statement in ("select text from developer_requests", "update developer_requests set status = 'failed'"):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))

    github.start_run(request_id, status="completed", conclusion="success")
    github.open_pull(request_id)
    github.set_ci("a" * 40, "success")
    assert asyncio.run(refresh_in_progress()) == 1
    assert asyncio.run(push_dispatcher.run_pending()).sent == 1
    assert sender.messages[0].data["developer_request_id"] == request_id
    with database_engine.connect() as connection:
        types = connection.execute(
            text("select type from domain_events where subject = :id order by created_at"), {"id": request_id}
        ).scalars().all()
    assert "developer_request.ready_for_review" in types

    with database_engine.begin() as connection:
        connection.execute(text("update developer_requests set status = 'merged' where id = :id"), {"id": request_id})
    assert discover_in_progress(TENANT_A) == []
