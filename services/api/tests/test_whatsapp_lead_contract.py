"""The contract step of the one lead list (docs/business.md#whatsapp-leads): the WhatsApp ingestion
writes the Business lead itself, never merges two people on its own, and the Sales overview reads
Business leads. ``sales_leads`` and ``GET /api/v1/sales/leads`` stay (expand first)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api import sales_whatsapp_ingestion as importer
from anum_api.business.store import business_store
from anum_api.business.whatsapp_leads import memory_whatsapp_leads
from anum_api.sales_lead_lifecycle import LeadStatus
from anum_api.schemas import TenantContext
from anum_api.whatsapp_store import Direction
from test_business_lead_sources import BASE, MEMBER, OTHER_WORKSPACE, OWNER, TENANT, VIEWER, WORKSPACE, lead_with_contact, waiting, whatsapp_lead

client = TestClient(main.app)
WEBHOOK = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="system:whatsapp", roles=[])


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    memory_whatsapp_leads.clear()
    yield
    business_store.clear()
    memory_whatsapp_leads.clear()


def leads(**params: object) -> list[dict]:
    response = client.get(f"{BASE}/leads", headers=OWNER, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_ingestion_adds_the_enquiry_to_the_lead_list_with_its_source_and_conversation() -> None:
    wa = whatsapp_lead("wa_new", "0501112233", company_name="Noor Trading")
    assert importer.join_lead_list(WEBHOOK, wa, "owner_unify") == "created"
    [lead] = leads(channel="whatsapp")
    assert lead["channel"] == "whatsapp" and lead["channel_conversation_id"] == "conv_wa_new" and lead["sales_lead_id"] == "wa_new"
    assert lead["channel_phone"] == "+971501112233" and lead["state"] == "discovered" and lead["source"] == "inbound"
    # Owned by the person who connected WhatsApp, not by the webhook.
    assert lead["owner_id"] == "owner_unify"
    memory_whatsapp_leads.put(wa)
    assert waiting()["total"] == 0  # already in the list: nothing waits
    # Idempotent: the same enquiry again changes nothing.
    assert importer.join_lead_list(WEBHOOK, wa, "owner_unify") == "unchanged"
    assert len(leads(channel="whatsapp")) == 1


def test_a_number_already_on_a_lead_waits_for_the_owner_and_is_never_merged() -> None:
    existing = lead_with_contact(phone="+971501234567", title="Sara's shop")
    wa = whatsapp_lead("wa_same", "0501234567")
    memory_whatsapp_leads.put(wa)
    assert importer.join_lead_list(WEBHOOK, wa, None) == "waiting"
    assert leads(channel="whatsapp") == []
    page = waiting()
    assert page["total"] == 1 and page["items"][0]["suggestions"][0]["lead_id"] == existing
    # The owner answers: different people. Then the enquiry is a lead of its own; nothing was merged.
    mapped = client.post(f"{BASE}/lead-sources/whatsapp/wa_same/map", headers=MEMBER, json={"separate_from": [existing]})
    assert mapped.status_code == 200, mapped.text
    assert importer.join_lead_list(WEBHOOK, wa, None) == "unchanged"
    titles = {lead["title"] for lead in leads()}
    assert "Sara's shop" in titles and len(leads(channel="whatsapp")) == 1


def test_a_linked_enquiry_stays_on_the_lead_the_owner_chose() -> None:
    existing = lead_with_contact(phone="+971509998877", title="Known customer")
    wa = whatsapp_lead("wa_link", "0509998877")
    memory_whatsapp_leads.put(wa)
    linked = client.post(f"{BASE}/lead-sources/whatsapp/wa_link/map", headers=MEMBER, json={"link_to_lead_id": existing})
    assert linked.status_code == 200, linked.text
    assert importer.join_lead_list(WEBHOOK, wa, None) == "unchanged"
    assert [lead["id"] for lead in leads(channel="whatsapp")] == [existing]


def test_the_importer_writes_the_crm_row_then_the_business_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(id="message-1", conversation_id="conv_ingest", body="I need an online store for my shop",
                              direction=Direction.INBOUND, kind="text")
    conversation = SimpleNamespace(id="conv_ingest", contact_id="contact-1")

    class WhatsAppStore:
        def __enter__(self) -> "WhatsAppStore":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def get_message(self, message_id: str) -> object:
            return message if message_id == message.id else None

        def get_conversation(self, conversation_id: str) -> object:
            return conversation if conversation_id == conversation.id else None

        def get_account(self) -> object:
            return SimpleNamespace(updated_by="owner_unify")

    inserted: list = []

    class Session:
        def __enter__(self) -> "Session":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def flush(self) -> None:
            return None

    class Crm:
        def __init__(self, *args: object) -> None:
            pass

        def find_by_inbound_message(self, _: str) -> object:
            return None

        def find_by_conversation(self, conversation_id: str) -> object:
            return next((lead for lead in inserted if lead.source_conversation_id == conversation_id), None)

        def insert(self, lead: object, event: object) -> None:
            inserted.append(lead)
            memory_whatsapp_leads.put(lead)  # type: ignore[arg-type]

    monkeypatch.setattr(importer, "open_whatsapp_store", lambda context: WhatsAppStore())
    monkeypatch.setattr(importer, "SessionLocal", SimpleNamespace(begin=lambda: Session()))
    monkeypatch.setattr(importer, "set_tenant_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(importer, "SalesCRMStore", Crm)
    event = [{"message_id": "message-1", "conversation_id": "conv_ingest"}]
    assert importer.import_verified_whatsapp_enquiry(WEBHOOK, event)["created"] == 1
    [lead] = leads(channel="whatsapp")
    assert lead["sales_lead_id"] == inserted[0].id and lead["channel_conversation_id"] == "conv_ingest"
    assert lead["owner_id"] == "owner_unify"
    # The same conversation again: the CRM row is a duplicate and the lead list is not doubled.
    assert importer.import_verified_whatsapp_enquiry(WEBHOOK, event)["duplicate"] == 1
    assert len(leads(channel="whatsapp")) == 1


def test_an_older_crm_row_joins_the_list_when_the_customer_writes_again() -> None:
    old = whatsapp_lead("wa_old", "0507776655", status=LeadStatus.QUALIFYING)
    memory_whatsapp_leads.put(old)
    assert waiting()["total"] == 1
    assert importer.join_lead_list(WEBHOOK, old, None) == "created"
    assert waiting()["total"] == 0


def test_a_lead_list_failure_keeps_the_crm_row_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.business import whatsapp_leads

    def broken(*args: object, **kwargs: object) -> str:
        raise RuntimeError("database gone")

    monkeypatch.setattr(whatsapp_leads, "join_from_ingestion", broken)
    wa = whatsapp_lead("wa_broken", "0503334444")
    memory_whatsapp_leads.put(wa)
    assert importer.join_lead_list(WEBHOOK, wa, None) is None
    assert waiting()["total"] == 1  # still visible to the owner, who can add it


def overview(headers: dict[str, str] = OWNER) -> dict:
    response = client.get("/api/v1/sales/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_sales_overview_reads_business_leads_and_counts_the_waiting_ones() -> None:
    assert overview()["leads"] == 0 and overview()["waiting"] == 0
    for index in range(3):
        wa = whatsapp_lead(f"wa_{index}", f"050100000{index}", minutes=index)
        memory_whatsapp_leads.put(wa)
        assert importer.join_lead_list(WEBHOOK, wa, "owner_unify") == "created"
    lost = whatsapp_lead("wa_lost", "0502000000", status=LeadStatus.LOST)
    memory_whatsapp_leads.put(lost)
    importer.join_lead_list(WEBHOOK, lost, None)
    memory_whatsapp_leads.put(whatsapp_lead("wa_wait", "0503000000"))  # not in the list yet
    lead_with_contact(phone="+971504000000", title="A lead from another source")
    figures = overview()
    assert figures["leads"] == 4 and figures["lost"] == 1 and figures["won"] == 0 and figures["quoted"] == 0
    assert figures["waiting"] == 1 and figures["source"] == "business_leads"
    assert figures["by_intent"] == {"website": 4}
    # Moved by Business's own state machine, the figures follow.
    first = next(lead for lead in leads(channel="whatsapp") if lead["sales_lead_id"] == "wa_0")
    lost_move = client.post(f"{BASE}/leads/{first['id']}/transition", headers=OWNER, json={"to": "lost", "reason": "Chose another shop"})
    assert lost_move.status_code == 200, lost_move.text
    assert overview()["lost"] == 2
    # Scoped to the workspace; viewers cannot read the WhatsApp figures.
    assert overview(OTHER_WORKSPACE)["leads"] == 0
    assert client.get("/api/v1/sales/overview", headers=VIEWER).status_code == 403


def test_the_old_list_is_kept_until_the_final_contract_step() -> None:
    paths = main.app.openapi()["paths"]
    assert "get" in paths["/api/v1/sales/leads"]
