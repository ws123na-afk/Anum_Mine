"""Supabase sign-in source for tyre planning (docs/tyre-planning.md#connecting), mocked HTTP.

ANUM signs in as a dedicated TyrePulse user, verifies the session with Supabase Auth,
reuses and refreshes it, reads PostgREST with the user's JWT, sends nothing but GETs to
tables, and refuses service_role keys. The real-database proof of isolation and write
rejection is in ``test_postgres_tyrepulse_reader.py``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from anum_api.main import app
from anum_api.tyre import sources
from anum_api.tyre.store import memory_tyre_store
from tyre_fixtures import build_rows

URL = "https://tyrepulse.example.supabase.co"
KEY = "sb_publishable_example"
EMAIL = "anum-reader@example.com"
USER_ID = "00000000-0000-0000-0000-0000000000a1"
SIGNING = "example" * 6
API = "/api/v1/tyre"
OWNER = {"x-tenant-id": "tenant_auth", "x-workspace-id": "workspace_auth", "x-user-id": "user_owner", "x-user-roles": "owner"}


def login(password: str = "example", key: str = KEY, email: str = EMAIL) -> str:
    return json.dumps({"api_key": key, "email": email, "password": password})


def legacy_key(role: str) -> str:
    return jwt.encode({"role": role, "iss": "supabase"}, SIGNING, algorithm="HS256")


class FakeAuth:
    """Supabase Auth and PostgREST: sign-in, refresh, user, table reads with the user's JWT."""

    def __init__(self) -> None:
        self.rows = build_rows()
        self.calls: list[tuple[str, str]] = []
        self.sign_ins = self.refreshes = 0
        self.lifetime = 3600
        self.role = "authenticated"
        self.user_id = USER_ID
        self.refresh_ok = True
        self.expire_next_read = False
        self.openapi_status = 403

    def token(self) -> dict[str, Any]:
        now = int(time.time())
        access = jwt.encode({"sub": USER_ID, "role": self.role, "exp": now + self.lifetime}, SIGNING, algorithm="HS256")
        return {"access_token": access, "refresh_token": f"refresh-{self.sign_ins}-{self.refreshes}",
                "expires_in": self.lifetime, "user": {"id": USER_ID}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = urlsplit(str(request.url))
        self.calls.append((request.method, parts.path))
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if request.headers.get("apikey") != KEY:
            return httpx.Response(401, json={"message": "Invalid API key"})
        if parts.path == "/auth/v1/token":
            body = json.loads(request.content)
            if query["grant_type"] == "password":
                self.sign_ins += 1
                if body != {"email": EMAIL, "password": "example"}:
                    code = "email_not_confirmed" if body.get("password") == "unconfirmed" else "invalid_credentials"
                    return httpx.Response(400, json={"error_code": code})
                return httpx.Response(200, json=self.token())
            self.refreshes += 1
            return httpx.Response(200, json=self.token()) if self.refresh_ok else httpx.Response(400, json={"error_code": "refresh_token_already_used"})
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            jwt.decode(bearer, SIGNING, algorithms=["HS256"])
        except jwt.PyJWTError:
            return httpx.Response(401, json={"message": "JWT expired"})
        if parts.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": self.user_id})
        if parts.path == "/rest/v1/":
            return httpx.Response(self.openapi_status, json={})
        if self.expire_next_read:
            self.expire_next_read = False
            return httpx.Response(401, json={"message": "JWT expired"})
        table = parts.path.removeprefix("/rest/v1/")
        if table not in self.rows:
            return httpx.Response(404, json={"code": "PGRST205"})
        select = query.get("select", "*")
        limit, offset = int(query.get("limit", "1000")), int(query.get("offset", "0"))
        page = self.rows[table][offset: offset + limit]
        if select != "*":
            page = [{c: row.get(c) for c in select.split(",")} for row in page]
        return httpx.Response(200, json=page)

    def factory(self):  # type: ignore[no-untyped-def]
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handler), timeout=5)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAuth]:
    sources.reset_auth_sessions()
    fake = FakeAuth()
    monkeypatch.setattr(sources, "rest_client_factory", fake.factory())
    yield fake
    sources.reset_auth_sessions()


def test_signs_in_once_verifies_and_reads_with_the_users_token(fake: FakeAuth) -> None:
    source = sources.SupabaseAuthSource(URL, login())
    message = asyncio.run(source.test())
    assert message.startswith(f"Signed in as {EMAIL} (read-only).") and "tyre_records 1" in message
    tables = {t.name: t for t in asyncio.run(source.discover())}  # openapi refused: probes the known tables
    assert {"vehicle_fleet", "tyre_records", "tyre_service_events", "tpms_readings", "suppliers", "inspections",
            "work_orders", "checklist_submissions", "work_order_line_items", "parts_consumption"} <= set(tables)
    assert "tyre_conditions" in {c.name for c in tables["inspections"].columns}
    assert "serial_no" in {c.name for c in tables["tyre_records"].columns}
    rows = asyncio.run(source.fetch("tyre_service_events", ["tyre_serial", "tread_depth"])).rows
    assert len(rows) == 98
    assert fake.sign_ins == 1 and ("GET", "/auth/v1/user") in fake.calls
    data_calls = {(m, p) for m, p in fake.calls if p.startswith("/rest/v1")}
    assert {m for m, _ in data_calls} == {"GET"} and not any("/rpc" in p for _, p in data_calls)


def test_reuses_refreshes_and_signs_in_again(fake: FakeAuth) -> None:
    source = sources.SupabaseAuthSource(URL, login())
    asyncio.run(source.fetch("vehicle_fleet", ["asset_no"]))
    asyncio.run(sources.SupabaseAuthSource(URL, login()).fetch("vehicle_fleet", ["asset_no"]))
    assert (fake.sign_ins, fake.refreshes) == (1, 0)  # one session per credential, in memory
    # Near expiry: the refresh token renews it.
    for session in sources._sessions.values():
        session.expires_at = time.time() + 10
    asyncio.run(source.fetch("vehicle_fleet", ["asset_no"]))
    assert (fake.sign_ins, fake.refreshes) == (1, 1)
    # Refresh token already used elsewhere: sign in again.
    fake.refresh_ok = False
    for session in sources._sessions.values():
        session.expires_at = 0
    asyncio.run(source.fetch("vehicle_fleet", ["asset_no"]))
    assert (fake.sign_ins, fake.refreshes) == (2, 2)
    # Revoked mid-way: one renewal and a retry.
    fake.refresh_ok = True
    fake.expire_next_read = True
    assert len(asyncio.run(source.fetch("vehicle_fleet", ["asset_no"])).rows) == 6
    # A changed password is a different session.
    with pytest.raises(sources.TyreSourceError, match="refused the reader's e-mail or password"):
        asyncio.run(sources.SupabaseAuthSource(URL, login("changed")).test())
    with pytest.raises(sources.TyreSourceError, match="not confirmed"):
        asyncio.run(sources.SupabaseAuthSource(URL, login("unconfirmed")).test())


def test_refuses_a_session_that_is_not_an_ordinary_verified_user(fake: FakeAuth) -> None:
    fake.role = "service_role"
    with pytest.raises(sources.TyreSourceError, match="role authenticated"):
        asyncio.run(sources.SupabaseAuthSource(URL, login()).test())
    sources.reset_auth_sessions()
    fake.role, fake.user_id = "authenticated", "00000000-0000-0000-0000-0000000000ff"
    with pytest.raises(sources.TyreSourceError, match="could not confirm"):
        asyncio.run(sources.SupabaseAuthSource(URL, login()).test())


def test_service_role_keys_are_refused_everywhere() -> None:
    for key in ("sb_secret_example", legacy_key("service_role"), legacy_key("supabase_admin")):
        with pytest.raises(sources.TyreSourceError, match="service_role"):
            sources.SupabaseRestSource(URL, key)
        with pytest.raises(sources.TyreSourceError, match="service_role"):
            sources.SupabaseAuthSource(URL, login(key=key))
    assert sources.supabase_key_role(legacy_key("anon")) == "anon"
    assert sources.supabase_key_role(KEY) == "anon"
    sources.SupabaseRestSource(URL, legacy_key("anon"))  # accepted
    for bad in ("not json", json.dumps({"api_key": KEY, "email": EMAIL}), login(email="not-an-email"), "[]"):
        with pytest.raises(sources.TyreSourceError):
            sources.parse_reader_login(bad)


def test_only_reads_leave_anum(fake: FakeAuth) -> None:
    for method, path in (("POST", "/rest/v1/tyre_records"), ("PATCH", "/rest/v1/tyre_records"),
                         ("DELETE", "/rest/v1/tyre_records"), ("GET", "/rest/v1/rpc/purge"), ("GET", "/rest/v1/rpc"),
                         ("POST", "/rest/v1/rpc/graphql"), ("GET", "/graphql/v1"), ("GET", "/storage/v1/object/x"),
                         ("GET", "/rest/v1/a/b"), ("POST", "/auth/v1/logout")):
        with pytest.raises(sources.TyreSourceError, match="only reads"):
            sources._assert_allowed_call(method, path)
    source = sources.SupabaseAuthSource(URL, login())
    with pytest.raises(sources.TyreSourceError, match="only reads"):
        asyncio.run(source.fetch("rpc", ["x"]))
    assert not any(path.startswith("/rest/v1/rpc") for _, path in fake.calls)


def test_the_connection_api_saves_and_hides_the_reader_login(fake: FakeAuth) -> None:
    memory_tyre_store.clear()
    client = TestClient(app)
    for secret, message in ((login(key="sb_secret_example"), "service_role"), (json.dumps({"email": EMAIL}), "publishable key")):
        refused = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_auth", "endpoint": URL, "secret": secret})
        assert refused.status_code == 422 and message in refused.json()["error"]["message"]
    rest = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_rest", "endpoint": URL, "secret": legacy_key("service_role")})
    assert rest.status_code == 422 and "service_role" in rest.json()["error"]["message"]
    saved = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_auth", "endpoint": URL, "secret": login()})
    assert saved.status_code == 200, saved.text
    connection = saved.json()["connection"]
    assert connection["secret_hint"] == f"{EMAIL} · key …mple" and connection["endpoint"] == URL
    assert '"password"' not in saved.text
    tested = client.post(f"{API}/connection/test", headers=OWNER).json()
    assert tested["ok"] is True
    found = client.post(f"{API}/discover", headers=OWNER).json()
    assert found["suggestion"]["preset"] == "tyrepulse"
    assert client.put(f"{API}/mapping", headers=OWNER, json=found["suggestion"]).status_code == 200
    synced = client.post(f"{API}/sync", headers=OWNER)
    assert synced.status_code == 200 and synced.json()["setup"]["sync"]["counts"]["tyres"] == 32
    # Changing only the label keeps the saved login.
    renamed = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_auth", "label": "TP", "endpoint": URL})
    assert renamed.status_code == 200 and renamed.json()["connection"]["secret_hint"].startswith(EMAIL)
    memory_tyre_store.clear()
