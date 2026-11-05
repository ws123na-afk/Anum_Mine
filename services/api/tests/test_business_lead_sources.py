"""One lead list (docs/business.md#whatsapp-leads): WhatsApp Sales CRM leads mapped into Business
leads, duplicate suggestions the owner answers, permissions and tenant isolation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.store import business_store
from anum_api.business.whatsapp_leads import memory_whatsapp_leads, pair_key
from anum_api.sales_lead_lifecycle import Lead as SalesLead
from anum_api.sales_lead_lifecycle import LeadStatus
from anum_api.whatsapp_sales import SalesIntent

client = TestClient(main.app)
TENANT, WORKSPACE = "tenant_unify", "workspace_unify"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_unify", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_unify", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_unify", "x-user-roles": "viewer"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_unify_other"}
OTHER_TENANT = {**OWNER, "x-tenant-id": "tenant_unify_other", "x-workspace-id": "workspace_unify_b"}
BASE = "/api/v1/business"
START = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)


def whatsapp_lead(lead_id: str, phone: str | None, *, status: LeadStatus = LeadStatus.NEW, tenant: str = TENANT,
                  workspace: str = WORKSPACE, minutes: int = 0, requirements: str = "Need a website with Arabic and English",
                  contact_name: str = "Sara", company_name: str = "") -> SalesLead:
    when = START + timedelta(minutes=minutes)
    return SalesLead(
        id=lead_id, tenant_id=tenant, workspace_id=workspace, contact_id=f"contact_{lead_id}", source_conversation_id=f"conv_{lead_id}",
        source_message_id=f"wamid_{lead_id}", source_campaign_hint=None, preferred_language="en", intent=SalesIntent.WEBSITE,
        status=status, created_at=when, updated_at=when, contact_name=contact_name, company_name=company_name, phone_e164=phone,
        requirements=requirements,
    )


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    memory_whatsapp_leads.clear()
    yield
    business_store.clear()
    memory_whatsapp_leads.clear()


def waiting(headers: dict[str, str] = OWNER, **params: object) -> dict:
    response = client.get(f"{BASE}/lead-sources/whatsapp", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def lead_with_contact(phone: str | None = None, email: str | None = None, title: str = "Existing lead") -> str:
    account = client.post(f"{BASE}/accounts", headers=OWNER, json={"name": f"{title} Co"}).json()
    contact = client.post(f"{BASE}/accounts/{account['id']}/contacts", headers=OWNER,
                          json={"name": "Sara Ali", "phone": phone, "email": email, "consent_basis": "inbound_inquiry"}).json()["contacts"][0]
    created = client.post(f"{BASE}/leads", headers=OWNER, json={"title": title, "account_id": account["id"], "contact_id": contact["id"]})
    assert created.status_code == 201, created.text
    return created.json()["lead"]["id"]


def test_a_mapped_whatsapp_lead_is_a_business_lead_with_its_source_and_conversation() -> None:
    memory_whatsapp_leads.put(whatsapp_lead("wa_1", "0501234567", company_name="Sara Bakery"))
    page = waiting()
    assert page["total"] == 1 and page["items"][0]["phone"] == "+971501234567" and page["items"][0]["suggestions"] == []
    mapped = client.post(f"{BASE}/lead-sources/whatsapp/wa_1/map", headers=MEMBER, json={})
    assert mapped.status_code == 200, mapped.text
    detail = client.get(f"{BASE}/leads/{mapped.json()['lead_id']}", headers=OWNER).json()
    lead = detail["lead"]
    assert lead["channel"] == "whatsapp" and lead["channel_conversation_id"] == "conv_wa_1" and lead["sales_lead_id"] == "wa_1"
    assert lead["channel_phone"] == "+971501234567" and lead["source"] == "inbound" and lead["state"] == "discovered"
    assert lead["title"] == "Need a website with Arabic and English" and detail["account"]["name"] == "Sara Bakery"
    # Business's state machine and score apply: nothing is qualified or scored by the mapping.
    assert lead["score_known"] == 0 and "qualified" in detail["allowed_transitions"]
    assert waiting()["total"] == 0
    again = client.post(f"{BASE}/lead-sources/whatsapp/wa_1/map", headers=OWNER, json={})
    assert again.status_code == 409
    listed = client.get(f"{BASE}/leads", headers=OWNER, params={"channel": "whatsapp"}).json()
    assert [item["id"] for item in listed["items"]] == [lead["id"]]
    assert client.get(f"{BASE}/leads", headers=OWNER, params={"channel": "other"}).json()["total"] == 0
    assert client.get(f"{BASE}/leads", headers=OWNER, params={"q": "+97150123"}).json()["total"] == 1


def test_a_closed_crm_lead_arrives_lost_with_the_reason() -> None:
    memory_whatsapp_leads.put(whatsapp_lead("wa_lost", "+971501111111", status=LeadStatus.DISQUALIFIED, requirements=""))
    lead_id = client.post(f"{BASE}/lead-sources/whatsapp/wa_lost/map", headers=OWNER, json={}).json()["lead_id"]
    lead = client.get(f"{BASE}/leads/{lead_id}", headers=OWNER).json()["lead"]
    assert lead["state"] == "lost" and "disqualified" in lead["lost_reason"] and lead["title"] == "WhatsApp enquiry from Sara"


def test_the_same_phone_is_a_suggestion_never_an_automatic_merge() -> None:
    existing = lead_with_contact(phone="+971 50 222 3333")
    memory_whatsapp_leads.put(whatsapp_lead("wa_dup", "00971502223333"))
    item = waiting()["items"][0]
    assert item["suggestions"] == [{"lead_id": existing, "title": "Existing lead", "reason": "Same phone number as this lead's contact."}]
    refused = client.post(f"{BASE}/lead-sources/whatsapp/wa_dup/map", headers=OWNER, json={})
    assert refused.status_code == 409 and "same person" in refused.json()["error"]["message"]
    # map-all leaves it for the owner.
    assert client.post(f"{BASE}/lead-sources/whatsapp/map-all", headers=OWNER).json() == {"mapped": 0, "waiting_for_owner": 1}
    linked = client.post(f"{BASE}/lead-sources/whatsapp/wa_dup/map", headers=OWNER, json={"link_to_lead_id": existing})
    assert linked.status_code == 200 and linked.json() == {"lead_id": existing, "linked": True}
    lead = client.get(f"{BASE}/leads/{existing}", headers=OWNER).json()
    assert lead["lead"]["sales_lead_id"] == "wa_dup" and lead["lead"]["channel"] == "whatsapp"
    assert any(item["kind"] == "whatsapp_linked" for item in lead["activities"])
    assert client.get(f"{BASE}/leads", headers=OWNER).json()["total"] == 1
    assert waiting()["total"] == 0


def test_the_owner_can_say_they_are_different_people() -> None:
    existing = lead_with_contact(phone="+971502223333")
    memory_whatsapp_leads.put(whatsapp_lead("wa_other", "+971502223333", contact_name="Omar"))
    created = client.post(f"{BASE}/lead-sources/whatsapp/wa_other/map", headers=OWNER, json={"separate_from": [existing]})
    assert created.status_code == 200 and created.json()["lead_id"] != existing and created.json()["linked"] is False
    assert client.get(f"{BASE}/leads", headers=OWNER).json()["total"] == 2
    missing = client.post(f"{BASE}/lead-sources/whatsapp/wa_other/map", headers=OWNER, json={"separate_from": ["blead_nope"]})
    assert missing.status_code in {404, 409}


def test_map_all_turns_two_enquiries_from_one_number_into_one_lead_and_one_question() -> None:
    memory_whatsapp_leads.put(whatsapp_lead("wa_a", "+971503334444", minutes=1))
    memory_whatsapp_leads.put(whatsapp_lead("wa_b", "+971503334444", minutes=2))
    memory_whatsapp_leads.put(whatsapp_lead("wa_c", None, minutes=3, contact_name="", requirements=""))
    result = client.post(f"{BASE}/lead-sources/whatsapp/map-all", headers=OWNER)
    assert result.json() == {"mapped": 2, "waiting_for_owner": 1}
    left = waiting()
    assert [item["id"] for item in left["items"]] == ["wa_b"]
    assert left["items"][0]["suggestions"][0]["reason"] == "Same WhatsApp number."
    titles = sorted(item["title"] for item in client.get(f"{BASE}/leads", headers=OWNER).json()["items"])
    assert titles == ["Need a website with Arabic and English", "WhatsApp enquiry"]


def test_duplicate_business_leads_are_suggested_and_the_owner_merges_or_keeps_them() -> None:
    first = lead_with_contact(email="sara@bakery.example", title="First")
    account = client.post(f"{BASE}/accounts", headers=OWNER, json={"name": "Second Co"}).json()
    second_contact = client.post(f"{BASE}/accounts/{account['id']}/contacts", headers=OWNER,
                                 json={"name": "Sara", "email": "other@bakery.example", "phone": "+971504445555", "consent_basis": "inbound_inquiry"}).json()["contacts"][0]
    second = client.post(f"{BASE}/leads", headers=OWNER, json={"title": "Second", "account_id": account["id"], "contact_id": second_contact["id"]}).json()["lead"]["id"]
    assert client.get(f"{BASE}/lead-duplicates", headers=OWNER).json() == []
    memory_whatsapp_leads.put(whatsapp_lead("wa_third", "+971504445555"))
    third = client.post(f"{BASE}/lead-sources/whatsapp/wa_third/map", headers=OWNER, json={"separate_from": [second]}).json()["lead_id"]
    # The WhatsApp enquiry was kept apart from the lead, but the two Business leads now share a number.
    pairs = client.get(f"{BASE}/lead-duplicates", headers=OWNER).json()
    assert {(p["first"]["id"], p["second"]["id"]) for p in pairs} in ({(second, third)}, {(third, second)})
    # Members may say "different people"; only owners merge (it closes a lead).
    assert client.post(f"{BASE}/lead-duplicates/merge", headers=MEMBER, json={"keep_id": second, "merge_id": third}).status_code == 403
    merged = client.post(f"{BASE}/lead-duplicates/merge", headers=OWNER, json={"keep_id": second, "merge_id": third})
    assert merged.status_code == 200 and merged.json() == {"kept_id": second, "closed_id": third}
    kept = client.get(f"{BASE}/leads/{second}", headers=OWNER).json()["lead"]
    closed = client.get(f"{BASE}/leads/{third}", headers=OWNER).json()["lead"]
    assert kept["sales_lead_id"] == "wa_third" and kept["channel"] == "whatsapp" and closed["sales_lead_id"] is None
    assert closed["state"] == "lost" and closed["lost_reason"].startswith("Duplicate of")
    assert client.get(f"{BASE}/lead-duplicates", headers=OWNER).json() == []
    assert client.post(f"{BASE}/lead-duplicates/merge", headers=OWNER, json={"keep_id": second, "merge_id": third}).status_code == 409
    assert first


def test_keep_separate_hides_the_pair() -> None:
    one = lead_with_contact(email="same@bakery.example", title="One")
    account = client.post(f"{BASE}/accounts", headers=OWNER, json={"name": "Two Co"}).json()
    contact = client.post(f"{BASE}/accounts/{account['id']}/contacts", headers=OWNER,
                          json={"name": "Huda", "phone": "+971 50 123 0000", "consent_basis": "inbound_inquiry"}).json()["contacts"][0]
    two = client.post(f"{BASE}/leads", headers=OWNER, json={"title": "Two", "account_id": account["id"], "contact_id": contact["id"]}).json()["lead"]["id"]
    memory_whatsapp_leads.put(whatsapp_lead("wa_two", "+971501230000"))
    client.post(f"{BASE}/lead-sources/whatsapp/wa_two/map", headers=OWNER, json={"link_to_lead_id": two})
    assert client.get(f"{BASE}/lead-duplicates", headers=OWNER).json() == []
    assert one and pair_key("lead:b", "lead:a") == "lead:a|lead:b"
    separate = client.post(f"{BASE}/lead-duplicates/separate", headers=MEMBER, json={"first_id": one, "second_id": two})
    assert separate.status_code == 200
    assert client.post(f"{BASE}/lead-duplicates/separate", headers=MEMBER, json={"first_id": one, "second_id": "blead_nope"}).status_code == 404
    assert client.post(f"{BASE}/lead-duplicates/separate", headers=MEMBER, json={"first_id": one, "second_id": one}).status_code == 422


def test_permissions() -> None:
    memory_whatsapp_leads.put(whatsapp_lead("wa_perm", "+971505556666"))
    assert client.get(f"{BASE}/lead-sources/whatsapp", headers=VIEWER).status_code == 403
    assert client.get(f"{BASE}/lead-duplicates", headers=VIEWER).status_code == 403
    assert client.post(f"{BASE}/lead-sources/whatsapp/wa_perm/map", headers=VIEWER, json={}).status_code == 403
    assert client.post(f"{BASE}/lead-sources/whatsapp/map-all", headers=MEMBER).status_code == 403
    assert client.post(f"{BASE}/lead-duplicates/separate", headers=VIEWER, json={"first_id": "a", "second_id": "b"}).status_code == 403
    assert waiting(MEMBER)["total"] == 1


def test_another_workspace_or_tenant_sees_and_maps_nothing() -> None:
    memory_whatsapp_leads.put(whatsapp_lead("wa_mine", "+971507778888"))
    memory_whatsapp_leads.put(whatsapp_lead("wa_theirs", "+971507778888", tenant="tenant_unify_other", workspace="workspace_unify_b"))
    assert [item["id"] for item in waiting()["items"]] == ["wa_mine"]
    assert waiting(OTHER_WORKSPACE)["total"] == 0
    assert [item["id"] for item in waiting(OTHER_TENANT)["items"]] == ["wa_theirs"]
    assert client.post(f"{BASE}/lead-sources/whatsapp/wa_theirs/map", headers=OWNER, json={}).status_code == 404
    mine = client.post(f"{BASE}/lead-sources/whatsapp/wa_mine/map", headers=OWNER, json={}).json()["lead_id"]
    # The other tenant's same number is not a suggestion against my lead, and my lead is invisible there.
    assert waiting(OTHER_TENANT)["items"][0]["suggestions"] == []
    assert client.post(f"{BASE}/lead-sources/whatsapp/wa_theirs/map", headers=OTHER_TENANT, json={"link_to_lead_id": mine}).status_code == 404
    assert client.get(f"{BASE}/leads/{mine}", headers=OTHER_WORKSPACE).status_code == 404


def test_search_and_paging_of_waiting_leads() -> None:
    for index in range(30):
        memory_whatsapp_leads.put(whatsapp_lead(f"wa_page_{index:02d}", f"+9715060000{index:02d}", minutes=index, contact_name=f"Person {index}"))
    first = waiting(limit=25)
    assert first["total"] == 30 and len(first["items"]) == 25 and first["truncated"] is False
    assert len(waiting(limit=25, offset=25)["items"]) == 5
    assert [item["id"] for item in waiting(q="Person 7")["items"]] == ["wa_page_07"]
