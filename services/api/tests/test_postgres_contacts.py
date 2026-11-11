"""The Contacts directory in PostgreSQL (migration ``0166_contacts_directory``, docs/contacts.md#data-model).

The API runs as the non-owner ``anum_test_app`` role: the five tables are under forced RLS on tenant
and workspace, the build links real Phone calls, Business and WhatsApp rows through the same policies,
merge and do-not-contact round-trip, the sending paths refuse a blocked channel, and ``anum_maintenance``
discovers only the scope columns of the three source tables for the daily job.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_calls import ACCOUNT_SID, AUTH_TOKEN, BASE as CALLS_BASE, KEY_SECRET, FakeGateway, Twilio
from test_whatsapp import APP_SECRET, CUSTOMER, PHONE_ID, TOKEN, VERIFY, WABA_ID, FakeMeta, inbound, sign

from anum_api import whatsapp
from anum_api.whatsapp import whatsapp_settings
from anum_api.calls import ai, service as calls_service
from anum_api.calls.config import call_settings
from anum_api.calls.provider import TwilioVoice
from anum_api.contacts import jobs
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings


pytestmark = pytest.mark.database

URL = "/api/v1/contacts"
TABLES = ("contacts", "contact_identifiers", "contact_links", "contact_do_not_contact", "contact_merge_proposals")


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, roles: str = "owner", user: str = "user_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


A = headers()
A_MEMBER = headers(roles="member", user="member_test")
B = headers(TENANT_B, WORKSPACE_B)


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    with database_engine.begin() as connection:
        connection.execute(text("grant anum_maintenance to current_user"))
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    # Phone calls through a fake Twilio and WhatsApp through a fake Graph API, so the sending paths run for real.
    twilio = Twilio()
    monkeypatch.setattr(call_settings, "public_base_url", CALLS_BASE)
    monkeypatch.setattr(calls_service, "provider_factory", lambda credentials: TwilioVoice(credentials, transport=httpx.MockTransport(twilio.handler)))
    monkeypatch.setattr(ai, "call_gateway", lambda context: FakeGateway())
    monkeypatch.setattr(whatsapp, "graph_transport", httpx.MockTransport(FakeMeta()))
    monkeypatch.setattr(whatsapp_settings, "graph_api_version", "v23.0")
    whatsapp.reset_limits()
    yield TestClient(app)
    whatsapp.reset_limits()


def ok(response: httpx.Response, code: int = 200) -> dict:  # type: ignore[type-arg]
    assert response.status_code == code, response.text
    return response.json() if response.content else {}


def _count(app_session: Callable[..., Iterator[Session]], sql: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        return int(session.execute(text(sql)).scalar_one())


def _sources(client: TestClient) -> dict:  # type: ignore[type-arg]
    call = ok(client.post("/api/v1/calls/contacts", headers=A_MEMBER, json={"name": "Sara Ahmed", "phone": "+971 50 123 4567", "company": "Tyre Co"}), 201)
    lead = ok(client.post("/api/v1/business/leads", headers=A_MEMBER, json={
        "title": "Tyre Co deal", "account": {"name": "Tyre Co", "website": "https://tyre.example"},
        "contact": {"name": "Sara Ahmed", "email": "sara@tyre.example", "phone": "0501234567", "consent_basis": "public_business_channel"},
    }), 201)
    ok(client.put("/api/v1/whatsapp/account", headers=A, json={
        "phone_number_id": PHONE_ID, "business_account_id": WABA_ID, "access_token": TOKEN, "app_secret": APP_SECRET, "verify_token": VERIFY,
    }))
    import json

    body = json.dumps(inbound("Hello")).encode()
    assert client.post("/api/v1/whatsapp/webhook", content=body, headers={"content-type": "application/json", "x-hub-signature-256": sign(body, APP_SECRET)}).status_code == 200
    conversations = ok(client.get("/api/v1/whatsapp/conversations", headers=A))
    return {"call": call, "lead": lead, "conversation": conversations[0]}


def test_build_merge_and_do_not_contact_round_trip_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    sources = _sources(client)
    report = ok(client.post(f"{URL}/directory/rebuild", headers=A_MEMBER))
    assert report == {"scanned": 3, "linked": 3, "people_created": 2, "proposals_created": 0, "complete": True}
    page = ok(client.get(URL, headers=A_MEMBER))
    assert page["total"] == 2 and page["counts"]["by_source"] == {"calls": 1, "whatsapp": 1}
    sara = next(p for p in page["items"] if p["name"] == "Sara Ahmed")
    customer = next(p for p in page["items"] if p["id"] != sara["id"])
    assert sorted(sara["sources"]) == ["business_contact", "call_contact"] and sara["emails"][0]["value"] == "sara@tyre.example"
    assert customer["phones"][0]["value"] == f"+{CUSTOMER}"
    detail = ok(client.get(f"{URL}/{sara['id']}", headers=A_MEMBER))
    hrefs = {link["kind"]: link["href"] for link in detail["links"]}
    assert hrefs["call_contact"] == f"#calls-contacts?contact={sources['call']['id']}" and hrefs["business_contact"].startswith("#business-accounts?account=")
    assert {c["channel"]: c["can_contact"] for c in detail["channels"]} == {"phone": True, "email": True, "whatsapp": True}
    # Idempotent, and the other tenant and workspace see nothing.
    assert ok(client.post(f"{URL}/directory/rebuild", headers=A_MEMBER))["linked"] == 0
    assert ok(client.get(URL, headers=B))["total"] == 0 and client.get(f"{URL}/{sara['id']}", headers=B).status_code == 404
    assert ok(client.get(URL, headers=headers(workspace_id=WORKSPACE_B)))["total"] == 0
    # A manual person with the customer's number under another name: a proposal, then a merge.
    other = ok(client.post(URL, headers=A_MEMBER, json={"name": "Walk-in customer", "phones": [f"+{CUSTOMER}"], "notes": "Asked for 205/55R16"}), 201)["person"]
    proposals = ok(client.get(f"{URL}/proposals", headers=A_MEMBER))
    assert proposals["total"] == 1 and proposals["items"][0]["reason"] == "same_phone"
    merged = ok(client.post(f"{URL}/{other['id']}/merge", headers=A_MEMBER, json={"other_id": customer["id"]}))["person"]
    assert merged["sources"] == ["whatsapp_contact"] and merged["notes"] == "Asked for 205/55R16" and [p["value"] for p in merged["phones"]] == [f"+{CUSTOMER}"]
    assert ok(client.get(f"{URL}/proposals", headers=A_MEMBER))["total"] == 0
    assert _count(app_session, "select count(*) from contacts") == 2
    assert _count(app_session, "select count(*) from contact_links") == 3
    # The merged person is gone, and so is the proposal that named it; the merge is the audit record.
    assert _count(app_session, "select count(*) from contact_merge_proposals") == 0
    assert _count(app_session, "select count(*) from audit_records where action = 'contacts.merged'") == 1
    # Do not contact: 201 then 200, enforced for a browser call and a WhatsApp reply, cleared by an owner.
    ok(client.put("/api/v1/calls/provider", headers=A, json={
        "account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN, "api_key_sid": "SKexamplekey0001", "api_key_secret": KEY_SECRET,
        "twiml_app_sid": "APexampleapp0001", "from_number": "+97145550100", "business_name": "ANUM Tyres",
    }))
    assert client.put(f"{URL}/{sara['id']}/do-not-contact", headers=A_MEMBER, json={"channel": "phone", "reason": "Asked us to stop calling"}).status_code == 201
    assert client.put(f"{URL}/{sara['id']}/do-not-contact", headers=A_MEMBER, json={"channel": "phone", "reason": "Asked again"}).status_code == 200
    refused = client.post("/api/v1/calls/provider/calls", headers=A_MEMBER, json={"contact_id": sources["call"]["id"]})
    assert refused.status_code == 409 and "asked not to be contacted by phone" in refused.json()["error"]["message"]
    assert client.put(f"{URL}/{merged['id']}/do-not-contact", headers=A_MEMBER, json={"channel": "whatsapp", "reason": "Asked to stop"}).status_code == 201
    refused = client.post(f"/api/v1/whatsapp/conversations/{sources['conversation']['id']}/send", headers=A, json={"text": "Yes, 4 in stock."})
    assert refused.status_code == 409 and "asked not to be contacted on WhatsApp" in refused.json()["error"]["message"]
    assert ok(client.get(URL, headers=A_MEMBER, params={"dnc": "any"}))["total"] == 2
    assert client.request("DELETE", f"{URL}/{sara['id']}/do-not-contact/phone", headers=A_MEMBER, json={"reason": "Changed their mind"}).status_code == 403
    assert client.request("DELETE", f"{URL}/{sara['id']}/do-not-contact/phone", headers=A, json={"reason": "Changed their mind"}).status_code == 204
    assert client.post("/api/v1/calls/provider/calls", headers=A_MEMBER, json={"contact_id": sources["call"]["id"]}).status_code == 201
    assert _count(app_session, "select count(*) from audit_records where action like 'contacts.%'") >= 8
    assert _count(app_session, "select count(*) from audit_records where action = 'contacts.send_refused'") == 2
    with app_session(tenant_context()) as session:
        texts = " ".join(str(row.metadata) for row in session.execute(text("select metadata from audit_records where action like 'contacts.%'")).all())
    assert "Sara" not in texts and "971501234567" not in texts and "tyre.example" not in texts
    export = client.get(f"{URL}/export", headers=A)
    assert export.status_code == 200 and "Sara Ahmed,Tyre Co" in export.text
    assert client.delete(f"{URL}/{merged['id']}", headers=A).status_code == 204
    assert _count(app_session, "select count(*) from contact_identifiers where contact_id = :id".replace(":id", f"'{merged['id']}'")) == 0


def test_rls_is_forced_and_refuses_rows_of_another_tenant(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    row = ("insert into contacts (id, tenant_id, workspace_id, name, source, created_by) values (:id, :tenant, :workspace, 'X', 'manual', 'u')")
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(row), {"id": "c_b", "tenant": TENANT_B, "workspace": WORKSPACE_B})
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(row), {"id": "c_w", "tenant": TENANT_A, "workspace": WORKSPACE_B})
    with app_session(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as session:
        session.execute(text(row), {"id": "c_a", "tenant": TENANT_A, "workspace": WORKSPACE_A})
        # The normalised-identifier check refuses a raw number.
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text("insert into contact_identifiers (id, tenant_id, workspace_id, contact_id, kind, value) values ('i_x', :t, :w, 'c_a', 'phone', '050 123')"),
                                {"t": TENANT_A, "w": WORKSPACE_A})
    assert _count(app_session, "select count(*) from contacts", TENANT_B, WORKSPACE_B) == 0
    assert _count(app_session, "select count(*) from contacts") == 1
    with app_session(tenant_context()) as session:
        for table in TABLES:
            assert session.execute(text("select relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one() is True
            policies = {r.policyname for r in session.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).all()}
            assert {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, table


def test_maintenance_role_discovers_scopes_only_and_the_daily_job_builds(client: TestClient, database_engine: Engine) -> None:
    _sources(client)
    assert jobs.discover() == [(TENANT_A, WORKSPACE_A)]
    with database_engine.connect() as connection:
        for statement in ("select name from call_contacts", "select phone from call_contacts", "select email from business_contacts",
                          "select name from business_contacts", "select wa_id from whatsapp_contacts", "select profile_name from whatsapp_contacts",
                          "select id from contacts", "update call_contacts set name = 'x'"):
            with pytest.raises(DBAPIError):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))
    assert jobs.run(dry_run=True)["scanned"] == 3
    report = jobs.run(batch_size=2)
    assert report["workspaces"] == 1 and report["linked"] == 3 and report["people_created"] == 2 and report["incomplete_workspaces"] == 0
    page = ok(client.get(URL, headers=A_MEMBER))
    assert page["total"] == 2 and all(p["created_by"] == jobs.SYSTEM_ACTOR for p in page["items"])
    assert jobs.run() == {"workspaces": 1, "scanned": 0, "linked": 0, "people_created": 0, "proposals_created": 0, "incomplete_workspaces": 0}


def test_list_searches_filters_sorts_and_pages_in_sql(client: TestClient) -> None:
    for i in range(12):
        ok(client.post(URL, headers=A_MEMBER, json={"name": f"Person {i:02d}", "organisation": "Acme" if i % 2 else "Beta_Co",
                                                     "phones": [f"+9715011100{i:02d}"]}), 201)
    first = ok(client.post(URL, headers=A_MEMBER, json={"name": "Aaa First", "emails": ["aaa@example.com"]}), 201)["person"]
    ok(client.put(f"{URL}/{first['id']}/do-not-contact", headers=A_MEMBER, json={"channel": "all", "reason": "Left the company"}), 201)
    page = ok(client.get(URL, headers=A_MEMBER, params={"limit": 5}))
    assert page["total"] == 13 and [p["name"] for p in page["items"]][:2] == ["Aaa First", "Person 00"]
    assert ok(client.get(URL, headers=A_MEMBER, params={"limit": 5, "offset": 10}))["items"][0]["name"] == "Person 09"
    assert ok(client.get(URL, headers=A_MEMBER, params={"q": "ACME"}))["total"] == 6
    # LIKE wildcards in the search are literal: "_" matches only an underscore.
    assert ok(client.get(URL, headers=A_MEMBER, params={"q": "beta_co"}))["total"] == 6
    assert ok(client.get(URL, headers=A_MEMBER, params={"q": "a%c"}))["total"] == 0
    assert ok(client.get(URL, headers=A_MEMBER, params={"q": "+971 50 111 0007"}))["total"] == 1
    assert ok(client.get(URL, headers=A_MEMBER, params={"q": "aaa@"}))["total"] == 1
    assert ok(client.get(URL, headers=A_MEMBER, params={"dnc": "email"}))["total"] == 1  # "all" covers e-mail
    assert ok(client.get(URL, headers=A_MEMBER, params={"dnc": "none"}))["total"] == 12
    assert ok(client.get(URL, headers=A_MEMBER, params={"source": "calls"}))["total"] == 0
    assert ok(client.get(URL, headers=A_MEMBER, params={"sort": "created", "limit": 1}))["items"][0]["name"] == "Aaa First"


def test_a_second_merge_of_the_same_pair_finds_one_gone(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    a = ok(client.post(URL, headers=A_MEMBER, json={"name": "Huda Ali", "phones": ["+971501110000"]}), 201)["person"]
    b = ok(client.post(URL, headers=A_MEMBER, json={"name": "H. Ali", "phones": ["+971501110000"]}), 201)["person"]
    ok(client.post(f"{URL}/{a['id']}/merge", headers=A_MEMBER, json={"other_id": b["id"]}))
    reverse = client.post(f"{URL}/{b['id']}/merge", headers=A_MEMBER, json={"other_id": a["id"]})
    assert reverse.status_code == 404
    assert _count(app_session, "select count(*) from contacts") == 1
    assert _count(app_session, "select count(*) from contact_identifiers") == 1
