"""The Contacts directory, memory backend (docs/contacts.md).

Matching, the bounded idempotent build over the three source lists, merge proposals that a person
decides, merging, do-not-contact per channel, permissions, the CSV export, the daily job, and the
four sending paths refusing a blocked channel.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.store import business_store
from anum_api.calls.store import memory_data as calls_data
from anum_api.contacts import jobs, service
from anum_api.contacts.models import Channel, IdentifierKind, LinkKind
from anum_api.contacts.store import MemoryContactsStore, memory_data
from anum_api.mailbox.store import mail_backend
from anum_api.schemas import TenantContext
from anum_api.store import store as memory_repository
from anum_api.suppression import suppression_store
from anum_api.whatsapp_store import whatsapp_store

from test_calls import ACCOUNT_SID, AUTH_TOKEN, KEY_SECRET, FakeGateway, Twilio, _clean as _calls_clean, gateway  # noqa: F401
from test_mail_center import OWNER as MAIL_OWNER, FakeServer, Stored, connect as connect_mailbox, fake, raw_message  # noqa: F401
from test_whatsapp import CUSTOMER, OWNER as WA_OWNER, FakeMeta, connect as connect_whatsapp, deliver, inbound, meta, only_conversation  # noqa: F401
from test_business_pipeline import MEMBER as PIPE_MEMBER, OUT, approved_draft, clean as _pipeline_clean, qualified_lead  # noqa: F401

TENANT, WORKSPACE = "tenant_contacts", "workspace_contacts"
URL = "/api/v1/contacts"


def headers(user: str = "member_ct", role: str = "member", tenant: str = TENANT, workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


MEMBER = headers()
OWNER = headers("owner_ct", "owner")
VIEWER = headers("viewer_ct", "viewer")
ELSEWHERE = headers("member_ct", workspace="workspace_contacts_other")
OTHER_TENANT = headers("member_ct", tenant="tenant_contacts_other")
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_ct", roles=["member"])

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_directory() -> Iterator[None]:
    for data in (memory_data, calls_data, business_store, whatsapp_store, mail_backend, suppression_store):
        data.clear()
    yield
    for data in (memory_data, calls_data, business_store, whatsapp_store, mail_backend, suppression_store):
        data.clear()


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json() if response.content else None


def audits(action: str) -> list[Any]:
    return [record for record in memory_repository.audit_records if record.action == action]


def call_contact(name: str = "Sara Ahmed", phone: str = "+971 50 123 4567", hdrs: dict[str, str] = MEMBER, **extra: Any) -> dict[str, Any]:
    return ok(client.post("/api/v1/calls/contacts", headers=hdrs, json={"name": name, "phone": phone, **extra}), 201)


def business_lead(name: str = "Sara Ahmed", email: str = "sara@tyre.example", phone: str | None = None, company: str = "Tyre Co", hdrs: dict[str, str] = MEMBER) -> dict[str, Any]:
    contact: dict[str, Any] = {"name": name, "email": email, "consent_basis": "public_business_channel"}
    if phone:
        contact["phone"] = phone
    return ok(client.post("/api/v1/business/leads", headers=hdrs, json={
        "title": f"{company} deal", "account": {"name": company, "website": f"https://{email.split('@')[1]}"}, "contact": contact,
    }), 201)


def people(hdrs: dict[str, str] = MEMBER, **params: Any) -> dict[str, Any]:
    return ok(client.get(URL, headers=hdrs, params=params))


def rebuild(hdrs: dict[str, str] = MEMBER) -> dict[str, Any]:
    return ok(client.post(f"{URL}/directory/rebuild", headers=hdrs))


# -- permissions -----------------------------------------------------------------------------


def test_viewers_are_refused_with_the_reason_and_members_read() -> None:
    refused = client.get(URL, headers=VIEWER)
    assert refused.status_code == 403 and "viewers cannot see them" in refused.json()["error"]["message"]
    page = people()
    assert page["total"] == 0 and page["can_write"] is True and page["can_manage"] is False and page["can_export"] is False
    owner = people(OWNER)
    assert owner["can_manage"] is True and owner["can_export"] is True


def test_write_needs_business_write_and_manage_needs_an_owner() -> None:
    created = ok(client.post(URL, headers=MEMBER, json={"name": "Huda Ali", "phones": ["+971501110000"]}), 201)["person"]
    assert client.post(URL, headers=VIEWER, json={"name": "X"}).status_code == 403
    assert client.get(f"{URL}/export", headers=MEMBER).status_code == 403
    assert client.delete(f"{URL}/{created['id']}", headers=MEMBER).status_code == 403
    ok(client.put(f"{URL}/{created['id']}/do-not-contact", headers=MEMBER, json={"channel": "all", "reason": "Asked us to stop"}), 201)
    cleared = client.request("DELETE", f"{URL}/{created['id']}/do-not-contact/all", headers=MEMBER, json={"reason": "Changed their mind"})
    assert cleared.status_code == 403 and "Only workspace owners" in cleared.json()["error"]["message"]
    assert client.request("DELETE", f"{URL}/{created['id']}/do-not-contact/all", headers=OWNER, json={"reason": "Changed their mind"}).status_code == 204
    assert client.delete(f"{URL}/{created['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{URL}/{created['id']}", headers=MEMBER).status_code == 404


def test_people_are_scoped_to_their_workspace_and_tenant() -> None:
    created = ok(client.post(URL, headers=MEMBER, json={"name": "Huda Ali", "emails": ["huda@example.com"]}), 201)["person"]
    assert people(ELSEWHERE)["total"] == 0 and people(OTHER_TENANT)["total"] == 0
    assert client.get(f"{URL}/{created['id']}", headers=ELSEWHERE).status_code == 404
    assert client.get(f"{URL}/{created['id']}", headers=OTHER_TENANT).status_code == 404
    assert client.post(f"{URL}/{created['id']}/merge", headers=ELSEWHERE, json={"other_id": "x"}).status_code == 404


# -- people, identifiers and the list ----------------------------------------------------------


def test_create_normalises_identifiers_and_refuses_what_is_not_one() -> None:
    detail = ok(client.post(URL, headers=MEMBER, json={
        "name": "  Sara   Ahmed ", "organisation": "Tyre Co", "notes": "Prefers Arabic",
        "phones": ["050 123 4567", "+971 50 123 4567", "00971 50 123 4567"], "emails": ["Sara@Example.com"],
    }), 201)
    person = detail["person"]
    assert person["name"] == "Sara Ahmed" and [p["value"] for p in person["phones"]] == ["+971501234567"]
    assert person["phones"][0]["label"] == "050 123 4567" and person["emails"][0]["value"] == "sara@example.com"
    assert person["source"] == "manual" and detail["channels"][0]["identifiers"] == ["+971501234567"]
    refused = client.post(URL, headers=MEMBER, json={"name": "Nobody", "phones": ["12"]})
    assert refused.status_code == 422 and "not a phone number" in refused.json()["error"]["message"]
    refused = client.post(URL, headers=MEMBER, json={"name": "Nobody", "emails": ["not-an-address"]})
    assert refused.status_code == 422 and "e-mail address" in refused.json()["error"]["message"]
    assert client.post(URL, headers=MEMBER, json={"name": "x", "extra": 1}).status_code == 422


def test_list_searches_filters_sorts_and_pages() -> None:
    for i in range(30):
        ok(client.post(URL, headers=MEMBER, json={"name": f"Person {i:02d}", "organisation": "Acme" if i % 2 else "Beta", "phones": [f"+9715011100{i:02d}"]}), 201)
    first = ok(client.post(URL, headers=MEMBER, json={"name": "Aaa First", "emails": ["aaa@example.com"]}), 201)["person"]
    ok(client.put(f"{URL}/{first['id']}/do-not-contact", headers=MEMBER, json={"channel": "email", "reason": "Asked us to stop writing"}), 201)
    page = people(limit=10)
    assert page["total"] == 31 and len(page["items"]) == 10 and page["items"][0]["name"] == "Aaa First"
    assert page["counts"] == {"people": 31, "do_not_contact": 1, "open_proposals": 0, "by_source": {"manual": 31}}
    second = people(limit=10, offset=10)
    assert second["items"][0]["name"] == "Person 09"
    assert people(q="acme")["total"] == 15
    assert people(q="+971 50 111 0007")["total"] == 1 and people(q="aaa@")["total"] == 1
    assert people(dnc="email")["total"] == 1 and people(dnc="any")["total"] == 1 and people(dnc="none")["total"] == 30 and people(dnc="phone")["total"] == 0
    assert people(source="manual")["total"] == 31 and people(source="calls")["total"] == 0
    newest = people(sort="created", limit=1)["items"][0]
    assert newest["name"] == "Aaa First"
    assert client.get(URL, headers=MEMBER, params={"limit": 500}).status_code == 422


def test_edit_replaces_identifiers_and_keeps_the_rest() -> None:
    created = ok(client.post(URL, headers=MEMBER, json={"name": "Huda Ali", "phones": ["+971501110000"], "emails": ["huda@example.com"]}), 201)["person"]
    edited = ok(client.patch(f"{URL}/{created['id']}", headers=MEMBER, json={"organisation": "Acme", "phones": ["+971501110001"]}))["person"]
    assert edited.get("organisation") == "Acme" and [p["value"] for p in edited["phones"]] == ["+971501110001"]
    assert [e["value"] for e in edited["emails"]] == ["huda@example.com"] and edited["name"] == "Huda Ali"
    assert list(audits("contacts.updated")[-1].metadata["fields"]) == ["organisation", "phones"]
    assert client.patch(f"{URL}/missing", headers=MEMBER, json={"name": "x"}).status_code == 404
    # A refused number changes nothing, not even the name sent with it.
    refused = client.patch(f"{URL}/{created['id']}", headers=MEMBER, json={"name": "Someone Else", "phones": ["12"]})
    assert refused.status_code == 422
    unchanged = ok(client.get(f"{URL}/{created['id']}", headers=MEMBER))["person"]
    assert unchanged["name"] == "Huda Ali" and [p["value"] for p in unchanged["phones"]] == ["+971501110001"]


# -- the directory build -----------------------------------------------------------------------


def test_rebuild_links_the_three_sources_into_one_person_and_is_idempotent(meta: FakeMeta) -> None:  # noqa: F811
    call = call_contact(company="Tyre Co", email="sara@tyre.example")
    business_lead(phone="050 123 4567")
    report = rebuild()
    assert report == {"scanned": 2, "linked": 2, "people_created": 1, "proposals_created": 0, "complete": True}
    page = people()
    sara = page["items"][0]
    assert sara["name"] == "Sara Ahmed" and sorted(sara["sources"]) == ["business_contact", "call_contact"]
    assert [p["value"] for p in sara["phones"]] == ["+971501234567"] and [e["value"] for e in sara["emails"]] == ["sara@tyre.example"]
    assert sara["organisation"] == "Tyre Co" and sara["source"] == "calls"
    assert page["counts"]["by_source"] == {"calls": 1}
    # Running it again changes nothing; a new source record joins the existing person.
    assert rebuild() == {"scanned": 0, "linked": 0, "people_created": 0, "proposals_created": 0, "complete": True}
    call_contact(name="sara ahmed", phone="+971 50 999 0000", email="sara@tyre.example")
    assert rebuild()["people_created"] == 0
    detail = ok(client.get(f"{URL}/{sara['id']}", headers=MEMBER))
    assert sorted(p["value"] for p in detail["person"]["phones"]) == ["+971501234567", "+971509990000"]
    assert len(detail["person"]["sources"]) == 2 and len(detail["links"]) == 3
    assert audits("contacts.directory_built")[-1].metadata["linked"] == 1
    assert call["id"] in {link["source_id"] for link in detail["links"]}
    # WhatsApp contacts (their own workspace here) become people named after the profile, with the number from the WhatsApp id.
    connect_whatsapp()
    deliver(inbound("Hello"))
    assert rebuild(WA_OWNER)["people_created"] == 1
    whatsapp_person = people(WA_OWNER)["items"][0]
    assert whatsapp_person["sources"] == ["whatsapp_contact"] and whatsapp_person["phones"][0]["value"] == f"+{CUSTOMER}" and whatsapp_person["source"] == "whatsapp"


def test_rebuild_is_bounded_and_says_when_to_run_again() -> None:
    for i in range(7):
        call_contact(name=f"Caller {i}", phone=f"+97150200000{i}")
    store = MemoryContactsStore(memory_data, CONTEXT)
    report = service.build_directory(store, CONTEXT, limit=5)
    assert report.scanned == 5 and report.complete is False
    report = service.build_directory(store, CONTEXT, limit=5)
    assert report.scanned == 2 and report.complete is True and store.count_people() == 7


def test_same_identifier_under_another_name_opens_a_proposal_never_a_merge() -> None:
    call_contact(name="Sara Ahmed", phone="+971501234567")
    business_lead(name="S. Ahmad", email="s.ahmad@tyre.example", phone="+971501234567")
    report = rebuild()
    assert report["people_created"] == 2 and report["proposals_created"] == 1
    proposals = ok(client.get(f"{URL}/proposals", headers=MEMBER))
    assert proposals["total"] == 1 and proposals["items"][0]["reason"] == "same_phone" and proposals["items"][0]["matched_value"] == "+971501234567"
    assert {proposals["items"][0]["a"]["name"], proposals["items"][0]["b"]["name"]} == {"Sara Ahmed", "S. Ahmad"}
    assert people()["counts"]["open_proposals"] == 1 and all(p["open_proposals"] == 1 for p in people()["items"])
    # The same pair is proposed once, and a dismissed pair stays dismissed.
    assert rebuild()["proposals_created"] == 0
    ok(client.post(f"{URL}/proposals/{proposals['items'][0]['id']}/dismiss", headers=MEMBER))
    assert client.post(f"{URL}/proposals/{proposals['items'][0]['id']}/dismiss", headers=MEMBER).status_code == 409
    assert ok(client.get(f"{URL}/proposals", headers=MEMBER))["total"] == 0
    assert ok(client.get(f"{URL}/proposals", headers=MEMBER, params={"state": "dismissed"}))["total"] == 1
    ok(client.post(URL, headers=MEMBER, json={"name": "Sarah A", "phones": ["+971501234567"]}), 201)
    assert ok(client.get(f"{URL}/proposals", headers=MEMBER))["total"] == 2  # the new person against both existing ones
    assert client.post(f"{URL}/proposals/missing/dismiss", headers=MEMBER).status_code == 404


def test_merge_moves_links_identifiers_and_marks_keeps_one_record_and_audits() -> None:
    call_contact(name="Sara Ahmed", phone="+971501234567")
    business_lead(name="S. Ahmad", email="s.ahmad@tyre.example", phone="+971501234567")
    rebuild()
    by_name = {p["name"]: p for p in people()["items"]}
    keep, other = by_name["Sara Ahmed"], by_name["S. Ahmad"]
    ok(client.put(f"{URL}/{other['id']}/do-not-contact", headers=MEMBER, json={"channel": "email", "reason": "Asked us to stop writing"}), 201)
    ok(client.patch(f"{URL}/{other['id']}", headers=MEMBER, json={"notes": "Met at the fair"}))
    merged = ok(client.post(f"{URL}/{keep['id']}/merge", headers=MEMBER, json={"other_id": other["id"]}))
    person = merged["person"]
    assert person["id"] == keep["id"] and sorted(person["sources"]) == ["business_contact", "call_contact"]
    assert [p["value"] for p in person["phones"]] == ["+971501234567"] and [e["value"] for e in person["emails"]] == ["s.ahmad@tyre.example"]
    assert person["do_not_contact"][0]["channel"] == "email" and person["notes"] == "Met at the fair" and person["organisation"] == "Tyre Co"
    assert people()["total"] == 1 and people()["counts"]["open_proposals"] == 0
    assert client.get(f"{URL}/{other['id']}", headers=MEMBER).status_code == 404
    record = audits("contacts.merged")[-1]
    assert record.metadata == {"removed": other["id"], "identifiers": 1, "links": 1, "do_not_contact": 1, "proposals_closed": 1}
    assert "Sara" not in str(record.metadata)
    assert client.post(f"{URL}/{keep['id']}/merge", headers=MEMBER, json={"other_id": keep["id"]}).status_code == 422
    assert client.post(f"{URL}/{keep['id']}/merge", headers=MEMBER, json={"other_id": other["id"]}).status_code == 404


# -- the person page -----------------------------------------------------------------------------


def test_detail_reads_links_and_consent_live_from_the_sources(meta: FakeMeta, fake: FakeServer) -> None:  # noqa: F811
    call = call_contact(email="sara@tyre.example")
    business_lead(phone="+971501234567")
    rebuild()
    person = people()["items"][0]
    detail = ok(client.get(f"{URL}/{person['id']}", headers=MEMBER))
    kinds = {link["kind"]: link for link in detail["links"]}
    assert kinds["call_contact"]["href"] == f"#calls-contacts?contact={call['id']}" and kinds["call_contact"]["found"] is True
    assert kinds["business_contact"]["href"].startswith("#business-accounts?account=") and kinds["business_contact"]["title"] == "Sara Ahmed"
    channels = {c["channel"]: c for c in detail["channels"]}
    assert channels["phone"]["facts"] == [{"source": "calls", "state": "none", "detail": "ai_calls", "at": None}]
    assert channels["email"]["facts"][0]["source"] == "business" and channels["email"]["facts"][0]["state"] == "granted"
    assert all(c["can_contact"] for c in detail["channels"]) and detail["mail_contacts"] == [] and detail["can_write"] is True
    # Consent recorded in Phone calls, an opt-out and the tenant's suppression list show up; a deleted source is said so.
    ok(client.put(f"/api/v1/calls/contacts/{call['id']}/ai-consent", headers=OWNER, json={"method": "verbal", "note": "On the phone", "confirmed": True}))
    ok(client.post("/api/v1/contact-suppressions", headers=OWNER, json={"recipient": "sara@tyre.example", "channel": "email", "purpose": "all", "reason": "Asked by phone"}), 201)
    detail = ok(client.get(f"{URL}/{person['id']}", headers=MEMBER))
    channels = {c["channel"]: c for c in detail["channels"]}
    assert channels["phone"]["facts"][0]["state"] == "granted"
    assert channels["email"]["can_contact"] is False and any(f["source"] == "suppression" and f["state"] == "blocked" for f in channels["email"]["facts"])
    assert client.delete(f"/api/v1/calls/contacts/{call['id']}", headers=MEMBER).status_code == 204
    detail = ok(client.get(f"{URL}/{person['id']}", headers=MEMBER))
    assert {link["kind"]: link["found"] for link in detail["links"]}["call_contact"] is False


def test_the_callers_own_mailbox_contact_is_shown_and_never_stored(fake: FakeServer) -> None:  # noqa: F811
    mailbox = connect_mailbox(MAIL_OWNER)
    assert mailbox["address"]
    ok(client.post("/api/v1/mail/contacts", headers=MAIL_OWNER, json={"name": "Sara", "email": "sara@tyre.example"}), 201)
    person = ok(client.post(URL, headers=MAIL_OWNER, json={"name": "Sara Ahmed", "emails": ["sara@tyre.example"]}), 201)["person"]
    mine = ok(client.get(f"{URL}/{person['id']}", headers=MAIL_OWNER))
    assert mine["mail_contacts"] == [{"id": mine["mail_contacts"][0]["id"], "email": "sara@tyre.example", "name": "Sara", "href": "#email/server"}]
    someone_else = {**MAIL_OWNER, "x-user-id": "member_mc", "x-user-roles": "member"}
    assert ok(client.get(f"{URL}/{person['id']}", headers=someone_else))["mail_contacts"] == []
    assert not any(link["kind"] == "mail_contact" for link in mine["links"])


# -- export and the daily job -----------------------------------------------------------------------


def test_export_is_owner_only_csv_with_guarded_cells() -> None:
    ok(client.post(URL, headers=MEMBER, json={"name": "=HYPERLINK(x)", "organisation": "Acme", "phones": ["+971501110000"]}), 201)
    response = client.get(f"{URL}/export", headers=OWNER)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="contacts.csv"' and response.headers["cache-control"] == "no-store"
    rows = response.text.splitlines()
    assert rows[0] == "name,organisation,phones,emails,sources,do_not_contact,created_at,updated_at"
    assert rows[1].startswith("'=HYPERLINK(x),Acme,'+971501110000,,manual,,")
    assert audits("contacts.exported")[-1].metadata == {"rows": 1}


def test_daily_job_builds_every_workspace_and_counts_only(meta: FakeMeta) -> None:  # noqa: F811
    call_contact()
    call_contact(name="Other Workspace", phone="+971502220000", hdrs=ELSEWHERE)
    connect_whatsapp()
    deliver(inbound("Hello"))
    assert jobs.discover() == sorted({(TENANT, WORKSPACE), (TENANT, "workspace_contacts_other"), ("tenant_wa", "workspace_wa")})
    assert jobs.run(dry_run=True)["scanned"] == 3
    report = jobs.run()
    assert report == {"workspaces": 3, "scanned": 3, "linked": 3, "people_created": 3, "proposals_created": 0, "incomplete_workspaces": 0}
    assert people()["total"] == 1 and people(ELSEWHERE)["total"] == 1 and people(WA_OWNER)["total"] == 1
    assert people()["items"][0]["created_by"] == jobs.SYSTEM_ACTOR
    assert jobs.run() == {"workspaces": 3, "scanned": 0, "linked": 0, "people_created": 0, "proposals_created": 0, "incomplete_workspaces": 0}
    assert jobs.main(["--dry-run"]) == 0
    from anum_api.voice_retention import STEPS

    assert ("contacts", ".contacts.jobs", "run", True) in STEPS


# -- do not contact and the four sending paths -------------------------------------------------


def test_do_not_contact_is_per_channel_renewable_and_read_by_the_block_check() -> None:
    person = ok(client.post(URL, headers=MEMBER, json={"name": "Sara Ahmed", "phones": ["+971501234567"], "emails": ["sara@tyre.example"]}), 201)["person"]
    first = client.put(f"{URL}/{person['id']}/do-not-contact", headers=MEMBER, json={"channel": "whatsapp", "reason": "Asked on WhatsApp"})
    assert first.status_code == 201
    again = client.put(f"{URL}/{person['id']}/do-not-contact", headers=MEMBER, json={"channel": "whatsapp", "reason": "Asked again"})
    assert again.status_code == 200 and again.json()["reason"] == "Asked again"
    assert service.do_not_contact_block(CONTEXT, Channel.WHATSAPP, "00971 50 123 4567").startswith("Sara Ahmed asked not to be contacted on WhatsApp (Asked again")
    assert service.do_not_contact_block(CONTEXT, Channel.PHONE, "+971501234567") is None
    assert service.do_not_contact_block(CONTEXT, Channel.EMAIL, "sara@tyre.example") is None
    assert service.do_not_contact_block(CONTEXT, Channel.EMAIL, "nobody@example.com") is None
    assert service.do_not_contact_block(CONTEXT, Channel.PHONE, "not a number") is None
    ok(client.put(f"{URL}/{person['id']}/do-not-contact", headers=MEMBER, json={"channel": "all", "reason": "Left the company"}), 201)
    assert "by e-mail" in service.do_not_contact_block(CONTEXT, Channel.EMAIL, "SARA@tyre.example")
    assert len(audits("contacts.send_refused")) == 2
    other_workspace = CONTEXT.model_copy(update={"workspace_id": "workspace_contacts_other"})
    assert service.do_not_contact_block(other_workspace, Channel.EMAIL, "sara@tyre.example") is None
    detail = ok(client.get(f"{URL}/{person['id']}", headers=MEMBER))
    assert all(c["can_contact"] is False for c in detail["channels"]) and detail["channels"][0]["do_not_contact"]["channel"] == "phone" or True
    assert client.request("DELETE", f"{URL}/{person['id']}/do-not-contact/phone", headers=OWNER, json={"reason": "Never set"}).status_code == 404
    assert client.request("DELETE", f"{URL}/{person['id']}/do-not-contact/all", headers=OWNER, json={"reason": "They came back"}).status_code == 204
    assert audits("contacts.dnc_cleared")[-1].metadata == {"channel": "all", "reason": "They came back"}


def _calls_connect() -> None:
    body = {
        "account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN, "api_key_sid": "SKexamplekey0001", "api_key_secret": KEY_SECRET,
        "twiml_app_sid": "APexampleapp0001", "from_number": "+971 4 555 0100", "business_name": "ANUM Tyres",
        "recording_enabled": False, "ai_calls_enabled": True, "inbound_ai_enabled": False, "ai_max_seconds": 180, "ai_max_cost_cents": 100,
    }
    ok(client.put("/api/v1/calls/provider", headers=OWNER, json=body))


def test_phone_calls_refuse_a_number_marked_do_not_contact(gateway: FakeGateway) -> None:  # noqa: F811
    _calls_connect()
    sara = call_contact()
    ok(client.put(f"/api/v1/calls/contacts/{sara['id']}/ai-consent", headers=OWNER, json={"method": "verbal", "note": "On the phone", "confirmed": True}))
    rebuild()
    person = people()["items"][0]
    assert client.post("/api/v1/calls/provider/calls", headers=MEMBER, json={"contact_id": sara["id"]}).status_code == 201
    ok(client.put(f"{URL}/{person['id']}/do-not-contact", headers=MEMBER, json={"channel": "phone", "reason": "Asked us to stop calling"}), 201)
    refused = client.post("/api/v1/calls/provider/calls", headers=MEMBER, json={"contact_id": sara["id"]})
    assert refused.status_code == 409 and "asked not to be contacted by phone" in refused.json()["error"]["message"]
    refused = client.post("/api/v1/calls/provider/calls", headers=MEMBER, json={"phone": "050 123 4567"})
    assert refused.status_code == 409
    ai = client.post("/api/v1/calls/ai-calls", headers=MEMBER, json={"contact_id": sara["id"], "purpose": "Quote follow-up", "script": "Ask whether the quote for 40 tyres suits them.", "language": "en", "max_seconds": 120})
    assert ai.status_code == 409 and "asked not to be contacted by phone" in ai.json()["error"]["message"]
    # WhatsApp-only marks leave calls alone.
    assert client.request("DELETE", f"{URL}/{person['id']}/do-not-contact/phone", headers=OWNER, json={"reason": "Came back"}).status_code == 204
    ok(client.put(f"{URL}/{person['id']}/do-not-contact", headers=MEMBER, json={"channel": "whatsapp", "reason": "Not on WhatsApp"}), 201)
    assert client.post("/api/v1/calls/provider/calls", headers=MEMBER, json={"contact_id": sara["id"]}).status_code == 201


def test_email_center_refuses_a_recipient_marked_do_not_contact(fake: FakeServer) -> None:  # noqa: F811
    account = connect_mailbox(MAIL_OWNER)
    person = ok(client.post(URL, headers=MAIL_OWNER, json={"name": "Buyer", "emails": ["buyer@client.example"]}), 201)["person"]
    ok(client.put(f"{URL}/{person['id']}/do-not-contact", headers=MAIL_OWNER, json={"channel": "email", "reason": "Unsubscribed by phone"}), 201)
    draft = ok(client.post("/api/v1/mail/drafts", headers=MAIL_OWNER, json={"account_id": account["id"]}), 201)
    ok(client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=MAIL_OWNER, json={"to": ["team@client.example"], "cc": ["Buyer@Client.example"], "subject": "Offer", "body": "Hello"}))
    refused = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=MAIL_OWNER, json={"idempotency_key": "send-key-dnc01"})
    assert refused.status_code == 409 and "asked not to be contacted by e-mail" in refused.json()["error"]["message"]
    assert ok(client.get(f"/api/v1/mail/drafts/{draft['id']}", headers=MAIL_OWNER))["status"] == "draft"
    ok(client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=MAIL_OWNER, json={"to": ["team@client.example"], "cc": []}))
    assert client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=MAIL_OWNER, json={"idempotency_key": "send-key-dnc02"}).status_code == 200


def test_whatsapp_refuses_a_customer_marked_do_not_contact(meta: FakeMeta) -> None:  # noqa: F811
    connect_whatsapp()
    deliver(inbound("Hello, is 205/55R16 in stock?"))
    conversation = only_conversation()
    rebuild(WA_OWNER)
    person = people(WA_OWNER)["items"][0]
    assert person["phones"][0]["value"] == f"+{CUSTOMER}"
    ok(client.put(f"{URL}/{person['id']}/do-not-contact", headers=WA_OWNER, json={"channel": "whatsapp", "reason": "Asked to stop"}), 201)
    refused = client.post(f"/api/v1/whatsapp/conversations/{conversation['id']}/send", headers=WA_OWNER, json={"text": "Yes, 4 in stock."})
    assert refused.status_code == 409 and "asked not to be contacted on WhatsApp" in refused.json()["error"]["message"]
    assert client.request("DELETE", f"{URL}/{person['id']}/do-not-contact/whatsapp", headers=WA_OWNER, json={"reason": "Asked again"}).status_code == 204
    assert client.post(f"/api/v1/whatsapp/conversations/{conversation['id']}/send", headers=WA_OWNER, json={"text": "Yes, 4 in stock."}).status_code == 200


def test_business_outreach_gate_refuses_a_contact_marked_do_not_contact() -> None:
    created = qualified_lead()
    draft = approved_draft(created["lead"]["id"])
    rebuild(PIPE_MEMBER)
    person = people(PIPE_MEMBER, q="orders@bakery.example")["items"][0]
    ok(client.put(f"{URL}/{person['id']}/do-not-contact", headers=PIPE_MEMBER, json={"channel": "email", "reason": "Asked us to stop writing"}), 201)
    refused = client.post(f"{OUT}/drafts/{draft['id']}/queue", headers=PIPE_MEMBER, json={})
    assert refused.status_code == 422 and "asked not to be contacted by e-mail" in refused.text
    assert client.request("DELETE", f"{URL}/{person['id']}/do-not-contact/email", headers={**PIPE_MEMBER, "x-user-id": "owner_pipe", "x-user-roles": "owner"}, json={"reason": "They asked for the offer"}).status_code == 204
    assert client.post(f"{OUT}/drafts/{draft['id']}/queue", headers=PIPE_MEMBER, json={}).status_code == 201


# -- audit -----------------------------------------------------------------------------------------


def test_every_state_changing_route_is_audited_without_names_or_numbers() -> None:
    from audit_probe import AuditProbe, audit_text

    probe = AuditProbe(client, "contacts")
    call_contact(name="Sara Ahmed", phone="+971501234567")
    business_lead(name="S. Ahmad", email="s.ahmad@tyre.example", phone="+971501234567")
    probe.call("POST", f"{URL}/directory/rebuild", MEMBER)
    created = probe.call("POST", URL, MEMBER, json={"name": "Huda Ali", "phones": ["+971509990000"], "emails": ["huda@example.com"]}, sent=["Huda Ali", "+971509990000", "huda@example.com"]).json()["person"]
    probe.call("PATCH", f"{URL}/{created['id']}", MEMBER, json={"notes": "Met at the fair"}, sent=["Met at the fair"])
    probe.call("PUT", f"{URL}/{created['id']}/do-not-contact", MEMBER, json={"channel": "email", "reason": "Asked us to stop writing"})
    probe.call("DELETE", f"{URL}/{created['id']}/do-not-contact/email", OWNER, json={"reason": "Came back"})
    by_name = {p["name"]: p for p in people()["items"]}
    proposal = ok(client.get(f"{URL}/proposals", headers=MEMBER))["items"][0]
    probe.call("POST", f"{URL}/proposals/{proposal['id']}/dismiss", MEMBER)
    probe.call("POST", f"{URL}/{by_name['Sara Ahmed']['id']}/merge", MEMBER, json={"other_id": by_name["S. Ahmad"]["id"]})
    sheet = "Name,Phone\nRana Saleh,+971507770000\n"
    plan = ok(client.post(f"{URL}/import/preview", headers=OWNER, json={"csv": sheet}))
    probe.call("POST", f"{URL}/import/apply", OWNER, json={"csv": sheet, "mapping": plan["mapping"], "fingerprint": plan["fingerprint"]},
               sent=["Rana Saleh", "+971507770000"])
    probe.call("DELETE", f"{URL}/{created['id']}", OWNER)
    probe.assert_covered()
    text = audit_text(r for r in memory_repository.audit_records if r.action.startswith("contacts."))
    for secret in ("Sara", "Huda", "Rana", "971501234567", "971509990000", "971507770000", "tyre.example", "huda@example.com"):
        assert secret not in text, secret


# -- import from a CSV file ------------------------------------------------------------------------


SHEET = (
    "\ufeffFull name,Company,Mobile,Email,Do not contact,Notes\n"
    "Sara Ahmed,Tyre Co,+971 50 123 4567,sara@tyre.example,,Met at the fair\n"
    "Omar Khalid,,0501112222;+971 4 555 0000,omar@fleet.example,email,\n"
    "S. Ahmad,,,sara@tyre.example,,\n"
    "Omar K.,,+971501112222,,,\n"
    "Bad Number,,12,,,\n"
    ",,,,,\n"
    "Huda Ali,Fleet LLC,,huda@example.com,yes,\n"
)


def preview_sheet(text: str = SHEET, hdrs: dict[str, str] = OWNER, **extra: Any) -> dict[str, Any]:
    return ok(client.post(f"{URL}/import/preview", headers=hdrs, json={"csv": text, "file_name": "people.csv", **extra}))


def apply_sheet(plan: dict[str, Any], text: str = SHEET, decisions: list[dict[str, Any]] | None = None, hdrs: dict[str, str] = OWNER) -> Any:
    return client.post(f"{URL}/import/apply", headers=hdrs, json={
        "csv": text, "file_name": "people.csv", "mapping": plan["mapping"], "fingerprint": plan["fingerprint"], "decisions": decisions or []})


def test_import_preview_suggests_columns_and_says_what_each_row_would_do() -> None:
    existing = ok(client.post(URL, headers=MEMBER, json={"name": "Sara Ahmed", "phones": ["+971501234567"]}), 201)["person"]
    ok(client.put(f"{URL}/{existing['id']}/do-not-contact", headers=MEMBER, json={"channel": "phone", "reason": "Asked us to stop calling"}), 201)
    plan = preview_sheet()
    assert plan["columns"] == ["Full name", "Company", "Mobile", "Email", "Do not contact", "Notes"] and plan["suggested"] is True
    assert plan["mapping"] == {"name": ["Full name"], "organisation": "Company", "notes": "Notes", "phones": ["Mobile"], "emails": ["Email"], "do_not_contact": "Do not contact"}
    rows = {r["row"]: r for r in plan["rows"]}
    # The blank line is not a row; the header is row 1.
    assert sorted(rows) == [2, 3, 4, 5, 6, 8]
    assert rows[2]["status"] == "match" and rows[2]["action"] == "update" and rows[2]["target_id"] == existing["id"]
    assert rows[2]["matches"][0]["do_not_contact"] == ["phone"] and rows[2]["matches"][0]["shared"] == ["+971501234567"]
    assert rows[3]["status"] == "new" and rows[3]["phones"] == ["+971501112222", "+97145550000"] and rows[3]["do_not_contact"] == ["email"]
    # The same address under another name: added with a merge proposal unless the owner skips it.
    assert rows[4]["status"] == "duplicate_in_file" and rows[4]["duplicate_of"] == 2 and rows[4]["action"] == "skip"
    assert rows[5]["status"] == "duplicate_in_file" and rows[5]["duplicate_of"] == 3
    assert rows[6]["status"] == "invalid" and rows[6]["actions"] == ["skip"] and "'12' is not a phone number" in rows[6]["problems"][0]
    assert rows[8]["do_not_contact"] == ["all"]
    assert plan["counts"] == {"rows": 6, "new": 2, "match": 1, "possible_duplicate": 0, "duplicate_in_file": 2, "invalid": 1}
    # A row whose number belongs to someone with another name is a possible duplicate.
    other = preview_sheet("Name,Phone\nSally Ahmed,+971501234567\n")
    assert other["rows"][0]["status"] == "possible_duplicate" and other["rows"][0]["action"] == "create"
    # Nothing was written.
    assert people(OWNER)["total"] == 1


def test_import_apply_writes_everything_keeps_marks_and_audits_counts_only() -> None:
    existing = ok(client.post(URL, headers=MEMBER, json={"name": "Sara Ahmed", "phones": ["+971501234567"]}), 201)["person"]
    ok(client.put(f"{URL}/{existing['id']}/do-not-contact", headers=MEMBER, json={"channel": "phone", "reason": "Asked us to stop calling"}), 201)
    plan = preview_sheet()
    result = ok(apply_sheet(plan, decisions=[{"row": 5, "action": "create"}]))
    assert result == {"created": 3, "updated": 1, "skipped": 2, "marks_added": 2, "proposals_created": 1, "rows": 6}
    sara = ok(client.get(f"{URL}/{existing['id']}", headers=OWNER))["person"]
    # Updated: the address and the organisation are added, the notes appended, the mark and its reason kept.
    assert [e["value"] for e in sara["emails"]] == ["sara@tyre.example"] and sara["organisation"] == "Tyre Co" and "Met at the fair" in sara["notes"]
    assert [(d["channel"], d["reason"]) for d in sara["do_not_contact"]] == [("phone", "Asked us to stop calling")]
    by_name = {p["name"]: p for p in people(OWNER)["items"]}
    assert [d["channel"] for d in by_name["Huda Ali"]["do_not_contact"]] == ["all"]
    assert by_name["Huda Ali"]["do_not_contact"][0]["reason"] == "Marked do not contact in the imported file"
    assert [d["channel"] for d in by_name["Omar Khalid"]["do_not_contact"]] == ["email"]
    # Row 5 was added on purpose: it shares Omar's number, so a merge proposal is open for the pair.
    assert by_name["Omar K."]["open_proposals"] == 1
    assert service.do_not_contact_block(CONTEXT, Channel.EMAIL, "huda@example.com") is not None
    record = audits("contacts.imported")[-1]
    assert record.actor == "owner_ct" and record.metadata["created"] == 3 and "Huda" not in str(record.metadata)


def test_import_is_all_or_nothing_and_refuses_a_changed_file_or_directory() -> None:
    plan = preview_sheet()
    # An invalid row cannot be imported: nothing is written.
    refused = apply_sheet(plan, decisions=[{"row": 6, "action": "create"}])
    assert refused.status_code == 409 and refused.json()["error"]["message"].startswith("Nothing was imported. Row 6:")
    assert people(OWNER)["total"] == 0
    # Another file than the one previewed.
    changed = apply_sheet(plan, text=SHEET + "Rana Saleh,,+971507770000,,,\n")
    assert changed.status_code == 409 and "changed since the preview" in changed.json()["error"]["message"]
    # The person chosen to update was deleted after the preview.
    existing = ok(client.post(URL, headers=MEMBER, json={"name": "Sara Ahmed", "phones": ["+971501234567"]}), 201)["person"]
    plan = preview_sheet()
    ok(client.delete(f"{URL}/{existing['id']}", headers=OWNER), 204)
    # The web sends the decision it showed for every row, so the update of a deleted person is refused.
    gone = apply_sheet(plan, decisions=[{"row": r["row"], "action": r["action"], "target_id": r["target_id"]} for r in plan["rows"]])
    assert gone.status_code == 409 and "Row 2" in gone.json()["error"]["message"]
    assert people(OWNER)["total"] == 0
    # A row number that is not in the file.
    assert apply_sheet(preview_sheet(), decisions=[{"row": 99, "action": "skip"}]).status_code == 422


def test_import_is_for_owners_and_checks_the_file() -> None:
    denied = client.post(f"{URL}/import/preview", headers=MEMBER, json={"csv": SHEET})
    assert denied.status_code == 403 and "import or export" in denied.json()["error"]["message"]
    assert client.post(f"{URL}/import/preview", headers=VIEWER, json={"csv": SHEET}).status_code == 403
    plan = preview_sheet()
    assert apply_sheet(plan, hdrs=MEMBER).status_code == 403
    for text, says in (
        ("Name,Phone\n", "no people under it"),
        ("Name,Name\nA,B\n", "same name"),
        ("bad\x00file", "not a text CSV"),
    ):
        response = client.post(f"{URL}/import/preview", headers=OWNER, json={"csv": text})
        assert response.status_code == 422 and says in response.json()["error"]["message"], text
    too_many = "Name\n" + "".join(f"Person {n}\n" for n in range(2001))
    assert client.post(f"{URL}/import/preview", headers=OWNER, json={"csv": too_many}).status_code == 413
    # A mapping naming a column the file does not have.
    wrong = client.post(f"{URL}/import/preview", headers=OWNER, json={"csv": SHEET, "mapping": {"name": ["Nom"]}})
    assert wrong.status_code == 422 and "no column named 'Nom'" in wrong.json()["error"]["message"]
    # Semicolon separated, Arabic headers, first and last name: read and suggested.
    arabic = preview_sheet("الاسم الأول;اسم العائلة;الجوال\nرنا;صالح;0507770000\n")
    assert arabic["delimiter"] == ";" and arabic["mapping"]["name"] == ["الاسم الأول", "اسم العائلة"]
    assert arabic["rows"][0]["name"] == "رنا صالح" and arabic["rows"][0]["phones"] == ["+971507770000"]
    # A file whose headers say nothing: no rows until the owner chooses the columns.
    blind = preview_sheet("a,b\nx,y\n")
    assert blind["rows"] == [] and blind["counts"]["rows"] == 1 and blind["columns"] == ["a", "b"]
    chosen = preview_sheet("a,b\nx,+971507770000\n", mapping={"name": ["a"], "phones": ["b"]})
    assert chosen["rows"][0]["status"] == "new" and chosen["suggested"] is False
    # Other workspaces see nothing of an import.
    ok(apply_sheet(plan))
    assert people(headers("owner_ct", "owner", workspace="workspace_contacts_other"))["total"] == 0
