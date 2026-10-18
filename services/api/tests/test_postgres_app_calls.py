"""In-app calls in PostgreSQL (migration 0072_app_calls).

The API runs as the non-owner ``anum_test_app`` role. ``app_calls`` and
``app_call_signals`` are under forced RLS: tenant and workspace isolation, a restrictive
policy that admits only the two participants, and a restrictive insert policy (only the
caller inserts a call, only the sender a signal). Nobody can ring a person of another
tenant or workspace, and nobody reads or writes a call they are not part of.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.app_calls import read_snapshot
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database

DEVICE_A = "device-alice-0000001"
DEVICE_B = "device-bob-000000001"
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"


def headers(user_id: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


def ctx(user_id: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=["member"])


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "calls_turn_urls", "turn:turn.example.test:3478")
    monkeypatch.setattr(settings, "calls_turn_secret", SecretStr("turn-secret-example-turn-secret-example"))
    members = [
        (TENANT_A, WORKSPACE_A, "alice"),
        (TENANT_A, WORKSPACE_A, "bob"),
        (TENANT_A, WORKSPACE_A, "carol"),
        (TENANT_A, WORKSPACE_A2, "dave"),
        (TENANT_B, WORKSPACE_B, "erin"),
    ]
    with database_engine.begin() as connection:
        for tenant_id, workspace_id, user_id in members:
            connection.execute(
                text(
                    "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                    "values (:user_id, :tenant_id, :workspace_id, 'member', true)"
                ),
                {"user_id": user_id, "tenant_id": tenant_id, "workspace_id": workspace_id},
            )
    return TestClient(app)


def _visible(app_session: Callable[..., Iterator[Session]], context: TenantContext, table: str) -> int:
    with app_session(context) as session:
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": context.user_id})
        # No predicate: only the row policies decide.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())


def _place(client: TestClient, caller: str = "alice", callee: str = "bob") -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/calls", headers=headers(caller), json={"callee_user_id": callee, "client_id": DEVICE_A})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_call_and_its_signals_are_visible_to_the_two_participants_only(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    call = _place(client)
    assert client.post(f"/api/v1/calls/{call['id']}/accept", headers=headers("bob"), json={"client_id": DEVICE_B}).status_code == 200
    sent = client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("alice"), json={"kind": "offer", "sdp": OFFER})
    assert sent.status_code == 202, sent.text
    assert _visible(app_session, ctx("alice"), "app_calls") == 1
    assert _visible(app_session, ctx("bob"), "app_calls") == 1
    assert _visible(app_session, ctx("bob"), "app_call_signals") == 1
    # A teammate of the same workspace, another workspace and another tenant: nothing.
    assert _visible(app_session, ctx("carol"), "app_calls") == 0
    assert _visible(app_session, ctx("carol"), "app_call_signals") == 0
    assert _visible(app_session, ctx("bob", workspace_id=WORKSPACE_A2), "app_calls") == 0
    assert _visible(app_session, ctx("bob", tenant_id=TENANT_B, workspace_id=WORKSPACE_B), "app_calls") == 0
    assert client.get(f"/api/v1/calls/{call['id']}", headers=headers("carol")).status_code == 404
    assert client.post(f"/api/v1/calls/{call['id']}/end", headers=headers("carol"), json={}).status_code == 404
    # The offer reaches bob's bound device through the stream's own short session.
    snapshot = read_snapshot(ctx("bob"), DEVICE_B)
    assert [s.kind.value for s in snapshot.signals] == ["offer"]
    ended = client.post(f"/api/v1/calls/{call['id']}/end", headers=headers("bob"), json={"reason": "hangup"})
    assert ended.json()["outcome"] == "completed"
    assert _visible(app_session, ctx("bob"), "app_call_signals") == 0


@pytest.mark.parametrize(
    ("callee", "tenant_id", "workspace_id"),
    [("dave", TENANT_A, WORKSPACE_A2), ("erin", TENANT_B, WORKSPACE_B)],
)
def test_nobody_rings_a_person_of_another_workspace_or_tenant(
    client: TestClient, app_session: Callable[..., Iterator[Session]], callee: str, tenant_id: str, workspace_id: str
) -> None:
    response = client.post("/api/v1/calls", headers=headers("alice"), json={"callee_user_id": callee, "client_id": DEVICE_A})
    assert response.status_code == 404
    assert _visible(app_session, ctx(callee, tenant_id=tenant_id, workspace_id=workspace_id), "app_calls") == 0
    assert _visible(app_session, ctx("alice"), "app_calls") == 0


def test_the_row_policies_refuse_forged_rows(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    insert_call = text(
        "insert into app_calls (id, tenant_id, workspace_id, caller_user_id, callee_user_id, caller_name, callee_name, "
        "caller_client_id, media, status) values (:id, :tenant, :workspace, :caller, :callee, 'x', 'y', :device, 'audio', 'ringing')"
    )

    def forge(context: TenantContext, **values: str) -> None:
        with app_session(context) as session:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": context.user_id})
            session.execute(insert_call, {"device": DEVICE_A, **values})

    # Carol may not insert a call in which alice rings bob, nor one in another tenant.
    with pytest.raises(DBAPIError):
        forge(ctx("carol"), id="call_forged_1", tenant=TENANT_A, workspace=WORKSPACE_A, caller="alice", callee="bob")
    with pytest.raises(DBAPIError):
        forge(ctx("alice"), id="call_forged_2", tenant=TENANT_B, workspace=WORKSPACE_B, caller="alice", callee="erin")
    # The callee cannot insert a call on the caller's behalf either.
    with pytest.raises(DBAPIError):
        forge(ctx("bob"), id="call_forged_3", tenant=TENANT_A, workspace=WORKSPACE_A, caller="alice", callee="bob")
    call = _place(client)
    client.post(f"/api/v1/calls/{call['id']}/accept", headers=headers("bob"), json={"client_id": DEVICE_B})
    # A non-participant cannot slip a signal into the call.
    with app_session(ctx("carol")) as session, pytest.raises(DBAPIError):
        session.execute(text("select set_config('anum.user_id', 'carol', true)"))
        session.execute(
            text(
                "insert into app_call_signals (id, tenant_id, workspace_id, call_id, sender_user_id, recipient_user_id, "
                "recipient_client_id, kind, payload) values ('sig_forged', :tenant, :workspace, :call, 'alice', 'bob', :device, "
                "'offer', '{}'::jsonb)"
            ),
            {"tenant": TENANT_A, "workspace": WORKSPACE_A, "call": call["id"], "device": DEVICE_B},
        )
    # And a participant cannot rewrite a call into another tenant.
    with app_session(ctx("alice")) as session:
        session.execute(text("select set_config('anum.user_id', 'alice', true)"))
        with pytest.raises(DBAPIError):
            session.execute(text("update app_calls set tenant_id = :tenant"), {"tenant": TENANT_B})


def test_busy_is_read_in_the_callees_own_context(client: TestClient) -> None:
    first = _place(client, "carol", "bob")
    client.post(f"/api/v1/calls/{first['id']}/accept", headers=headers("bob"), json={"client_id": DEVICE_B})
    busy = _place(client, "alice", "bob")
    assert busy["outcome"] == "busy"
    notes = client.get("/api/v1/notifications", headers=headers("bob")).json()["items"]
    assert [n["kind"] for n in notes] == ["missed_call"]


def test_delete_my_data_rewrites_my_side_and_keeps_the_other_persons_history(
    client: TestClient, app_session: Callable[..., Iterator[Session]], database_engine: Engine
) -> None:
    call = _place(client)
    client.post(f"/api/v1/calls/{call['id']}/accept", headers=headers("bob"), json={"client_id": DEVICE_B})
    client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("alice"), json={"kind": "offer", "sdp": OFFER})
    exported = client.get("/api/v1/privacy/export", headers=headers("alice")).json()["app_calls"]
    assert [(c["direction"], c["peer_name"]) for c in exported] == [("outgoing", "bob")]
    assert "peer_user_id" not in exported[0]
    deleted = client.post("/api/v1/privacy/delete", headers=headers("alice"), json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["app_calls"] == 1 and deleted.json()["deleted"]["app_call_signals"] == 1
    assert _visible(app_session, ctx("alice"), "app_calls") == 0
    assert _visible(app_session, ctx("bob"), "app_call_signals") == 0
    items = client.get("/api/v1/calls", headers=headers("bob")).json()["items"]
    assert [(i["peer_name"], i["status"], i["outcome"]) for i in items] == [("Deleted user", "ended", "failed")]
    with database_engine.connect() as connection:
        row = connection.execute(text("select caller_user_id, caller_name, caller_client_id from app_calls")).one()
    assert row.caller_user_id.startswith("deleted:") and row.caller_name == "Deleted user" and row.caller_client_id == "deleted"


def test_the_daily_retention_job_discovers_old_calls_as_maintenance(
    client: TestClient, database_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    from anum_api.app_calls import purge_retention
    from anum_api.voice_retention import main as retention_main

    old = _place(client)
    client.post(f"/api/v1/calls/{old['id']}/end", headers=headers("alice"), json={"reason": "hangup"})
    recent = _place(client, "carol", "bob")
    client.post(f"/api/v1/calls/{recent['id']}/end", headers=headers("carol"), json={"reason": "hangup"})
    with database_engine.begin() as connection:
        connection.execute(text("update app_calls set ended_at = now() - interval '400 days' where id = :id"), {"id": old["id"]})
    # The maintenance role reads five columns of ended calls only, never names or devices.
    with database_engine.connect() as connection:  # rolled back on close
        connection.execute(text("set local role anum_maintenance"))
        rows = connection.execute(text("select id from app_calls")).scalars().all()
        assert set(rows) == {old["id"]}
    with database_engine.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("set local role anum_maintenance"))
        connection.execute(text("select caller_name from app_calls"))
    assert purge_retention(retention_days=365, dry_run=True) == {"retention_days": 365, "dry_run": True, "calls": 1}
    assert retention_main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["app_calls"] == {"retention_days": 365, "dry_run": False, "calls": 1}
    with database_engine.connect() as connection:
        assert connection.execute(text("select id from app_calls")).scalars().all() == [recent["id"]]
