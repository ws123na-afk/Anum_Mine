"""Phone calls in PostgreSQL (migration ``0070_calls``, docs/calls.md#storage).

The API runs as the non-owner ``anum_test_app`` role. Every calls table is under forced RLS on
tenant and workspace; ``anum_maintenance`` finds webhook routes, key rotation work and expired
recordings from a few columns only, never a number, a note or a ciphertext.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_calls import ACCOUNT_SID, AUTH_TOKEN, BASE, KEY_SECRET, FakeGateway, Twilio

from anum_api.calls import ai, service
from anum_api.calls.config import call_settings
from anum_api.calls.provider import TwilioVoice, twilio_signature
from anum_api.calls.retention import purge_recordings
from anum_api.db import session as db_session
from anum_api.files import InMemoryObjectStorage, file_store
from anum_api.main import app
from anum_api.rotate_secrets import rotate_call_provider_secrets
from anum_api.schemas import utc_now
from anum_api.secret_box import SecretCipher, reset_secret_cipher
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(user_id: str = "owner_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


A = headers()
A_MEMBER = headers("member_test", role="member")
B = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None) -> sessionmaker[Session]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    with database_engine.begin() as connection:
        connection.execute(text("grant anum_maintenance to current_user"))
    return factory


@pytest.fixture
def twilio(app_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Iterator[Twilio]:
    fake = Twilio()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(db_session, "SessionLocal", app_factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(call_settings, "public_base_url", BASE)
    monkeypatch.setattr(service, "provider_factory", lambda credentials: TwilioVoice(credentials, transport=transport))
    monkeypatch.setattr(file_store, "storage", InMemoryObjectStorage())
    yield fake


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    fake = FakeGateway()
    monkeypatch.setattr(ai, "call_gateway", lambda context: fake)
    return fake


@pytest.fixture
def client(twilio: Twilio) -> TestClient:
    return TestClient(app)


def _connect(client: TestClient, hdrs: dict[str, str] = A) -> None:
    response = client.put("/api/v1/calls/provider", headers=hdrs, json={
        "account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN, "api_key_sid": "SKexamplekey0001", "api_key_secret": KEY_SECRET,
        "twiml_app_sid": "APexampleapp0001", "from_number": "+97145550100", "business_name": "ANUM Tyres",
        "recording_enabled": True, "ai_calls_enabled": True,
    })
    assert response.status_code == 200, response.text


def _route(engine: Engine, tenant_id: str = TENANT_A) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text("select route_id from call_provider_accounts where tenant_id = :t"), {"t": tenant_id}).scalar_one())


def _hook(client: TestClient, route: str, path: str, params: dict[str, str]) -> httpx.Response:
    url = f"{BASE}/api/v1/calls/webhooks/twilio/{route}/{path}"
    return client.post(url.removeprefix(BASE), data=params, headers={"x-twilio-signature": twilio_signature(AUTH_TOKEN, url, params)})


def test_contacts_and_calls_are_isolated_per_tenant_under_forced_rls(client: TestClient, database_engine: Engine, gateway: FakeGateway) -> None:
    sara = client.post("/api/v1/calls/contacts", headers=A_MEMBER, json={"name": "Sara Ahmed", "phone": "+971501234567"}).json()
    call = client.post("/api/v1/calls/log", headers=A_MEMBER, json={"contact_id": sara["id"], "outcome": "answered", "notes": "Quote"})
    assert call.status_code == 201, call.text
    assert client.get("/api/v1/calls/log", headers=A_MEMBER, params={"q": "quote"}).json()["total"] == 1
    assert client.get("/api/v1/calls/contacts", headers=B).json()["total"] == 0
    assert client.get(f"/api/v1/calls/contacts/{sara['id']}", headers=B).status_code == 404
    assert client.get(f"/api/v1/calls/log/{call.json()['id']}", headers=B).status_code == 404
    assert client.patch(f"/api/v1/calls/contacts/{sara['id']}", headers=B, json={"favourite": True}).status_code == 404
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"), {"t": TENANT_B, "w": WORKSPACE_B})
            assert connection.execute(text("select count(*) from call_contacts")).scalar_one() == 0
            assert connection.execute(text("select count(*) from call_log")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text(f"set local role {APP_ROLE}"))
                connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"), {"t": TENANT_B, "w": WORKSPACE_B})
                connection.execute(text(
                    "insert into call_contacts (id, tenant_id, workspace_id, created_by, name, phone, phone_normalized) "
                    "values ('c_x', :t, :w, 'u', 'X', '+971500000000', '+971500000000')"
                ), {"t": TENANT_A, "w": WORKSPACE_A})


def test_maintenance_role_sees_routes_and_recording_expiry_only(client: TestClient, database_engine: Engine, gateway: FakeGateway) -> None:
    _connect(client)
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select tenant_id, workspace_id, route_id from call_provider_accounts")).all()
            assert [(row.tenant_id, row.workspace_id) for row in rows] == [(TENANT_A, WORKSPACE_A)]
        for statement in (
            "select credentials_ciphertext from call_provider_accounts",
            "select account_sid from call_provider_accounts",
            "select phone from call_log",
            "select notes from call_log",
            "select phone from call_contacts",
            "update call_provider_accounts set business_name = 'x'",
        ):
            with pytest.raises(DBAPIError):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


def test_browser_call_recording_and_retention_end_to_end(client: TestClient, database_engine: Engine, gateway: FakeGateway) -> None:
    _connect(client)
    prepared = client.post("/api/v1/calls/provider/calls", headers=A_MEMBER, json={"phone": "+971501234567", "record": True, "recording_consent": True})
    assert prepared.status_code == 201, prepared.text
    call, identity = prepared.json()["call"], prepared.json()["identity"]
    route = _route(database_engine)
    xml = _hook(client, route, "voice", {"CallSid": "CApg0001", "From": f"client:{identity}", "anum_call_id": call["id"]}).text
    assert ">+971501234567</Number>" in xml and "recorded" in xml
    _hook(client, route, f"status/{call['id']}", {"CallSid": "CAchild", "CallStatus": "completed", "CallDuration": "30"})
    assert client.get(f"/api/v1/calls/log/{call['id']}", headers=A_MEMBER).json()["outcome"] == "answered"
    stored = _hook(client, route, f"recording/{call['id']}", {"RecordingStatus": "completed", "RecordingUrl": f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Recordings/RE1"})
    assert stored.status_code == 204
    logged = client.get(f"/api/v1/calls/log/{call['id']}", headers=A_MEMBER).json()
    assert logged["recording_file_id"]
    assert purge_recordings().deleted == 0
    assert purge_recordings(now=utc_now() + timedelta(days=40)).deleted == 1
    assert client.get(f"/api/v1/calls/log/{call['id']}", headers=A_MEMBER).json()["recording_file_id"] is None
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from workspace_files where id = :id"), {"id": logged["recording_file_id"]}).scalar_one() == 0


def test_ai_call_approval_binds_the_number_in_postgres(client: TestClient, twilio: Twilio, gateway: FakeGateway) -> None:
    _connect(client)
    sara = client.post("/api/v1/calls/contacts", headers=A_MEMBER, json={"name": "Sara Ahmed", "phone": "+971501234567"}).json()
    client.put(f"/api/v1/calls/contacts/{sara['id']}/ai-consent", headers=A, json={"method": "written", "confirmed": True})
    asked = client.post("/api/v1/calls/ai-calls", headers=A_MEMBER, json={"contact_id": sara["id"], "purpose": "Confirm delivery", "script": "Ask whether Thursday suits.", "max_seconds": 60})
    assert asked.status_code == 201, asked.text
    request = asked.json()
    client.patch(f"/api/v1/calls/contacts/{sara['id']}", headers=A_MEMBER, json={"phone": "+971559999999"})
    client.put(f"/api/v1/calls/contacts/{sara['id']}/ai-consent", headers=A, json={"method": "written", "confirmed": True})
    decided = client.post(f"/api/v1/approvals/{request['approval']['id']}/approve", headers=A, json={"payload_hash": request["approval"]["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert twilio.placed() == []
    current = client.get(f"/api/v1/calls/ai-calls/{request['id']}", headers=A_MEMBER).json()
    assert current["status"] == "failed" and "number changed" in current["error"]
    # The happy path in PostgreSQL: a new request for the new number is placed once approved.
    again = client.post("/api/v1/calls/ai-calls", headers=A_MEMBER, json={"contact_id": sara["id"], "purpose": "Confirm delivery", "script": "Ask whether Thursday suits.", "max_seconds": 60}).json()
    client.post(f"/api/v1/approvals/{again['approval']['id']}/approve", headers=A, json={"payload_hash": again["approval"]["payload_hash"]})
    (placed,) = twilio.placed()
    assert placed["To"] == ["+971559999999"]


def test_provider_credentials_are_re_encrypted_with_the_new_key(
    client: TestClient, database_engine: Engine, app_factory: sessionmaker[Session], gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, new = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setattr(settings, "secrets_key", SecretStr(old.decode()))
    reset_secret_cipher()
    try:
        _connect(client)
        _connect(client, B)
        report = rotate_call_provider_secrets(SecretCipher([new, old]), session_factory=app_factory)
        assert (report.rotated, report.failed) == (2, 0)
        with database_engine.connect() as connection:
            values = connection.execute(text("select credentials_ciphertext from call_provider_accounts")).scalars().all()
        assert all(SecretCipher([new]).is_current(value) for value in values)
        assert all(AUTH_TOKEN not in value for value in values)
        assert rotate_call_provider_secrets(SecretCipher([new, old]), session_factory=app_factory).already_current == 2
    finally:
        reset_secret_cipher()
