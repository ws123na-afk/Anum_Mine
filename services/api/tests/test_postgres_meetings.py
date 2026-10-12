"""Calls & Meetings in PostgreSQL (migration 0043_meetings), as the non-owner app role.

Meetings and transcript lines are workspace-shared under forced RLS; connector accounts
are private to one user. Background processing uses a fake model gateway.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from conftest import (
    APP_ROLE,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    tenant_context,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import meetings
from anum_api.db import session as db_session
from anum_api.files import InMemoryObjectStorage, file_store
from anum_api.main import app
from anum_api.meeting_connectors import (
    ConnectorProvider,
    StoredConnection,
    connection_store,
    open_connection_store,
)
from anum_api.meetings import meeting_store
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database

VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
<v Jane Doe>Welcome to the PMV planning call.</v>

00:00:05.000 --> 00:00:09.000
<v Omar Ali>I will send the tyre stock report by Friday.</v>
"""


class FakeGateway:
    provider = "test"
    model = "test-model"

    async def complete_chat(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> ModelResponse:
        answer = {
            "summary": "Reviewed the PMV plan.",
            "decisions": ["Restock first"],
            "action_items": [{"text": "Send the report", "owner": "Omar Ali", "due_date": None}],
            "follow_up_email": {"subject": "Recap", "body": "Thanks."},
        }
        return ModelResponse(text=json.dumps(answer), usage=ModelUsage(input_tokens=1, output_tokens=1, provider="test", model="test-model"))


def headers(user_id: str = "user_test", workspace_id: str = WORKSPACE_A, tenant_id: str = TENANT_A, role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(meetings, "analysis_gateway", lambda context: FakeGateway())
    monkeypatch.setattr(file_store, "storage", InMemoryObjectStorage())
    meeting_store.clear()
    connection_store.clear()
    yield TestClient(app)
    meeting_store.clear()
    connection_store.clear()


def _upload(client: TestClient, hdrs: dict[str, str] | None = None, title: str = "PMV planning") -> dict:
    response = client.post(
        "/api/v1/meetings/transcripts",
        content=VTT.encode(),
        headers={**(hdrs or headers()), "x-file-name": "pmv.vtt"},
        params={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _count(app_session: Callable[..., Iterator[Session]], table: str, context: TenantContext, user_id: str | None = None) -> int:
    with app_session(context) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608 - fixed table names


def test_meetings_persist_and_are_processed_in_postgres(client: TestClient) -> None:
    meeting = _upload(client)
    assert meeting_store.meetings == {}  # nothing in process memory
    stored = client.get(f"/api/v1/meetings/{meeting['id']}", headers=headers()).json()
    assert stored["status"] == "ready" and stored["summary"] == "Reviewed the PMV plan."
    assert stored["action_items"][0]["owner"] == "Omar Ali"

    page = client.get("/api/v1/meetings", params={"q": "tyre_stock%"}, headers=headers()).json()
    assert page["total"] == 0  # LIKE wildcards in the query are literal
    page = client.get("/api/v1/meetings", params={"q": "TYRE stock"}, headers=headers()).json()
    assert page["total"] == 1
    transcript = client.get(f"/api/v1/meetings/{meeting['id']}/transcript", params={"speaker": "Omar Ali"}, headers=headers()).json()
    assert transcript["total"] == 1 and transcript["speakers"] == ["Jane Doe", "Omar Ali"]

    item = stored["action_items"][0]
    created = client.post(f"/api/v1/meetings/{meeting['id']}/action-items/{item['id']}/task", headers=headers())
    assert created.status_code == 200, created.text
    assert client.post(f"/api/v1/meetings/{meeting['id']}/action-items/{item['id']}/task", headers=headers()).status_code == 409

    assert client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers()).status_code == 204
    assert client.get("/api/v1/meetings", headers=headers()).json()["total"] == 0


def test_meetings_are_isolated_per_workspace_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    _upload(client)
    _upload(client, headers(workspace_id=WORKSPACE_B, tenant_id=TENANT_B))
    a, a2, b = tenant_context(TENANT_A, WORKSPACE_A), tenant_context(TENANT_A, WORKSPACE_A2), tenant_context(TENANT_B, WORKSPACE_B)
    assert _count(app_session, "meetings", a) == 1
    assert _count(app_session, "meeting_transcript_segments", a) == 2
    assert _count(app_session, "meetings", a2) == 0
    assert _count(app_session, "meeting_transcript_segments", a2) == 0
    assert _count(app_session, "meetings", b) == 1
    assert client.get("/api/v1/meetings", headers=headers(workspace_id=WORKSPACE_A2)).json()["total"] == 0

    # Writing a meeting into another workspace is refused by WITH CHECK.
    with pytest.raises(DBAPIError):
        with app_session(a) as session:
            session.execute(
                text(
                    "insert into meetings (id, tenant_id, workspace_id, created_by, title, source, status) "
                    "values ('meeting_x', :t, :w, 'u', 't', 'transcript', 'ready')"
                ),
                {"t": TENANT_B, "w": WORKSPACE_B},
            )


def test_connector_accounts_are_private_to_their_user(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    mine = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test", roles=["member"])
    now = utc_now()
    with open_connection_store(mine) as store:
        store.save(
            StoredConnection(
                tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test", provider=ConnectorProvider.ZOOM,
                account_label="owner@example.com", token_ciphertext="sealed", expires_at=now, connected_at=now, updated_at=now,
            )
        )
    context = tenant_context(TENANT_A, WORKSPACE_A)
    assert _count(app_session, "meeting_connector_accounts", context, "user_test") == 1
    assert _count(app_session, "meeting_connector_accounts", context, "user_other") == 0
    assert _count(app_session, "meeting_connector_accounts", context) == 0
    other = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_other", roles=["member"])
    with open_connection_store(other) as store:
        assert store.get(other, ConnectorProvider.ZOOM) is None
        assert store.delete(other, ConnectorProvider.ZOOM) is False
    setup = client.get("/api/v1/meetings/setup", headers=headers()).json()
    assert next(item for item in setup["connectors"] if item["provider"] == "zoom")["connected"] is True
    assert next(item for item in client.get("/api/v1/meetings/setup", headers=headers("user_other")).json()["connectors"] if item["provider"] == "zoom")["connected"] is False


def test_privacy_delete_removes_my_meetings_only(client: TestClient, database_engine: Engine) -> None:
    _upload(client)
    theirs = _upload(client, headers("user_other"))
    summary = client.get("/api/v1/privacy", headers=headers()).json()["private"]
    assert summary["meetings"] == 1 and summary["meeting_transcript_segments"] == 2
    export = client.get("/api/v1/privacy/export", headers=headers()).json()
    assert [item["title"] for item in export["meetings_imported"]] == ["PMV planning"]
    deleted = client.post("/api/v1/privacy/delete", headers=headers(), json={"confirm": "DELETE MY DATA"}).json()["deleted"]
    assert deleted["meetings"] == 1
    with database_engine.connect() as connection:
        rows = connection.execute(text("select id from meetings")).all()
        segments = connection.execute(text("select count(*) from meeting_transcript_segments")).scalar_one()
    assert [row[0] for row in rows] == [theirs["id"]] and segments == 2


def test_connector_tokens_are_rotated_per_user_and_maintenance_sees_scope_only(
    client: TestClient, database_engine: Engine
) -> None:
    from cryptography.fernet import Fernet

    from anum_api.rotate_secrets import rotate_meeting_connector_tokens
    from anum_api.secret_box import SecretCipher

    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    old = SecretCipher([old_key])
    now = utc_now()
    for user in ("user_test", "user_other"):
        context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user, roles=["member"])
        with open_connection_store(context) as store:
            store.save(
                StoredConnection(
                    tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user, provider=ConnectorProvider.ZOOM,
                    account_label=f"{user}@example.com", token_ciphertext=old.encrypt(f"example-{user}"),
                    expires_at=now, connected_at=now, updated_at=now,
                )
            )
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select tenant_id, user_id, provider from meeting_connector_accounts order by 2")).all()
        assert [tuple(row) for row in rows] == [(TENANT_A, "user_other", "zoom"), (TENANT_A, "user_test", "zoom")]
        with pytest.raises(DBAPIError, match="permission denied"):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select token_ciphertext from meeting_connector_accounts"))

    factory = db_session.SessionLocal
    cipher = SecretCipher([new_key, old_key])
    report = rotate_meeting_connector_tokens(cipher, session_factory=factory, batch_size=1)
    assert (report.scanned, report.rotated, report.failed) == (2, 2, 0)
    with database_engine.connect() as connection:
        stored = dict(connection.execute(text("select user_id, token_ciphertext from meeting_connector_accounts")).all())
    for user, ciphertext in stored.items():
        assert Fernet(new_key).decrypt(ciphertext.encode()).decode() == f"example-{user}"
    assert rotate_meeting_connector_tokens(cipher, session_factory=factory).already_current == 2
