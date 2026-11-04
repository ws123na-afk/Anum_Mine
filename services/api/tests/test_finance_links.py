"""Finance links, business allocations and scoped iPhone device tokens (docs/finance-links.md,
docs/iphone-integrations.md), in-memory stores.

Every message here is SYNTHETIC: written for these tests, labelled, and not the wording of any
real bank. ``test_postgres_finance_links.py`` repeats the database checks under RLS.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import devices as D
from anum_api.finance import links as K
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now

TENANT, WORKSPACE, BUSINESS, OTHER_BUSINESS = "tenant_links", "ws_personal", "ws_business", "ws_business_two"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_links", "x-user-roles": "owner"}
BUSINESS_MEMBER = {"x-tenant-id": TENANT, "x-workspace-id": BUSINESS, "x-user-id": "colleague", "x-user-roles": "member"}
BUSINESS_OWNER_VIEW = {**OWNER, "x-workspace-id": BUSINESS}
OTHER_TENANT = {**BUSINESS_MEMBER, "x-tenant-id": "tenant_elsewhere"}
API = "/api/v1/finance"
IPHONE = "/api/v1/integrations/iphone"
SENDER = "TestBank"  # SYNTHETIC sender
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    memberships = dict(memory_repository.store.memberships)
    now = utc_now()
    for workspace, user, role in ((BUSINESS, "owner_links", "owner"), (BUSINESS, "colleague", "member"), (OTHER_BUSINESS, "owner_links", "viewer")):
        memory_repository.store.memberships[(TENANT, workspace, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=workspace, user_id=user, role=role, active=True, created_at=now, updated_at=now)
    K.register_target("vehicle", lambda context, kind, target: kind == "vehicle" and target.startswith("veh_"))
    yield
    K.unregister_target("vehicle")
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def account(**body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def put_settings(**body: Any) -> dict:
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    response = client.put(f"{API}/sms/settings", headers=OWNER, json={"version": version, **body})
    assert response.status_code == 200, response.text
    return response.json()


def setup() -> dict[str, Any]:
    account(name="Current", last4="5678")
    card = account(name="Card", kind="card", last4="1234")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    put_settings(enabled=True, extra_senders=[{"sender": SENDER, "bank": "test_bank"}], verified_senders=[SENDER],
                 account_map=[{"last4": "1234", "account_id": card["id"]}], wallet_cards=[{"card": "My Visa", "account_id": card["id"]}])
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    assert device.status_code == 201, device.text
    return {"card": card, "token": device.json()["token"], "device": device.json()["device"]}


def send(token: str, message: str = FUEL_SMS) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text
    return response.json()


def wallet(token: str, amount: str = "SAR 150.00") -> dict:
    response = client.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {token}"},
                           json={"source": "apple_wallet", "amount": amount, "merchant": "Petromin Fuel", "card": "My Visa", "name": "Purchase"})
    assert response.status_code == 200, response.text
    return response.json()


def fuel_payment() -> tuple[dict[str, Any], str]:
    """SAR 150 fuel reported by Apple Pay and by the bank SMS: one canonical transaction."""
    s = setup()
    assert wallet(s["token"])["status"] == "posted"
    assert send(s["token"])["status"] == "attached"
    entries = [e for e in sms_memory_store.entries.values()]
    assert len(entries) == 1
    return s, entries[0].canonical_id or entries[0].id


def journal() -> tuple[dict[str, Any], int]:
    snapshot = {e.id: (e.status, sorted((p.ledger_account, p.amount, p.currency) for p in e.postings)) for e in sms_memory_store.entries.values()}
    return snapshot, len(finance_store.transactions)


def link(canonical: str, amount: str | None = "150", **body: Any):  # type: ignore[no-untyped-def]
    payload = {"canonical_id": canonical, "dimension": "vehicle", "role": "payment", "target_type": "vehicle", "target_id": "veh_car1",
               "amount": amount, "currency": "SAR" if amount is not None else None, **body}
    return client.post(f"{API}/links", headers=OWNER, json=payload)


def allocate(canonical: str, amount: str = "150", workspace: str = BUSINESS, headers: dict | None = None, **body: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/allocations", headers={**OWNER, **(headers or {})},
                       json={"canonical_id": canonical, "workspace_id": workspace, "amount": amount, "currency": "SAR", "category": "fuel",
                             "memo": "Site visit", **body})


def correct(message_id: str, amount: str, account_id: str) -> None:
    response = client.post(f"{API}/sms/messages/{message_id}/post", headers=OWNER, json={"amount": amount, "account_id": account_id})
    assert response.status_code == 200, response.text


# -- cross-module acceptance ----------------------------------------------------------------------


def test_one_fuel_payment_linked_to_a_vehicle_and_allocated_to_a_business_adds_no_posting() -> None:
    _s, canonical = fuel_payment()
    before = journal()
    board = client.get(f"{API}/ledger/dashboard", headers=OWNER, params={"month": utc_now().strftime("%Y-%m")}).json()
    vehicle = link(canonical)
    assert vehicle.status_code == 201, vehicle.text
    business = allocate(canonical)
    assert business.status_code == 201, business.text
    # Two views of one posting: the journal and the stage 2 rows are exactly as they were.
    assert journal() == before
    after = client.get(f"{API}/ledger/dashboard", headers=OWNER, params={"month": utc_now().strftime("%Y-%m")}).json()
    assert after["expenses"] == board["expenses"] == [{"currency": "SAR", "amount": "150.00"}]
    summary = client.get(f"{API}/links/transactions/{canonical}", headers=OWNER).json()
    assert list(summary["amounts"]) == ["SAR"] and Decimal(summary["amounts"]["SAR"]) == Decimal(150)
    assert Decimal(summary["remaining"]["vehicle"]["SAR"]) == 0 and Decimal(summary["remaining"]["business"]["SAR"]) == 0
    assert Decimal(summary["remaining"]["purchase"]["SAR"]) == 150
    assert {l["dimension"] for l in summary["links"]} == {"vehicle", "business"}
    # The links remember which source events stood behind the payment (Apple Pay and the SMS).
    assert len(vehicle.json()["link"]["source_event_ids"]) == 2


def test_over_allocation_is_refused_per_dimension() -> None:
    _s, canonical = fuel_payment()
    assert link(canonical, "100").status_code == 201
    refused = link(canonical, "60", target_id="veh_car2")
    assert refused.status_code == 409
    assert refused.json()["code"] == "over_allocated" and Decimal(refused.json()["remaining"]) == 50
    assert link(canonical, "50", target_id="veh_car2").status_code == 201
    # Another dimension is its own view of the same 150.
    assert allocate(canonical, "150").status_code == 201
    again = allocate(canonical, "1", workspace=BUSINESS)  # the same target again with another amount reuses the derived key
    assert again.status_code == 409 and again.json()["code"] == "idempotency_conflict"
    wrong_currency = link(canonical, "10", currency="USD", target_id="veh_car3")
    assert wrong_currency.status_code == 422 and wrong_currency.json()["code"] == "currency_mismatch"


def test_concurrent_link_attempts_cannot_exceed_the_amount() -> None:
    _s, canonical = fuel_payment()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: link(canonical, "100", target_id=f"veh_car{i}").status_code, range(6)))
    assert sorted(results) == [201, 409, 409, 409, 409, 409]
    live = [l for l in links_memory_store.links.values() if l.status == "active"]
    assert sum(l.amount for l in live) == Decimal("100")


def test_idempotent_link_replays_and_a_reused_key_for_another_link_conflicts() -> None:
    _s, canonical = fuel_payment()
    first = link(canonical, "40")
    again = link(canonical, "40")
    assert first.status_code == 201 and again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["link"]["id"] == first.json()["link"]["id"]
    keyed = client.post(f"{API}/links", headers={**OWNER, "Idempotency-Key": "k-1"},
                        json={"canonical_id": canonical, "dimension": "vehicle", "role": "payment", "target_type": "vehicle", "target_id": "veh_b",
                              "amount": "10", "currency": "SAR"})
    assert keyed.status_code == 201
    clash = client.post(f"{API}/links", headers={**OWNER, "Idempotency-Key": "k-1"},
                        json={"canonical_id": canonical, "dimension": "vehicle", "role": "payment", "target_type": "vehicle", "target_id": "veh_c",
                              "amount": "10", "currency": "SAR"})
    assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"


def test_a_correction_keeps_links_and_a_smaller_amount_puts_them_in_review() -> None:
    s, canonical = fuel_payment()
    message_id = next(m.id for m in sms_memory_store.messages.values() if m.source == "wallet")
    vehicle = link(canonical, "150").json()["link"]
    allocation = allocate(canonical, "150").json()["link"]
    correct(message_id, "150.00", s["card"]["id"])  # same figures: a new entry in the same chain, links untouched
    assert links_memory_store.links[vehicle["id"]].status == "active" and links_memory_store.links[vehicle["id"]].canonical_id == canonical
    correct(message_id, "120.00", s["card"]["id"])
    assert links_memory_store.links[vehicle["id"]].status == "needs_review"
    rows = client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_MEMBER).json()["items"]
    assert [r["status"] for r in rows] == ["needs_review"]
    refused = client.post(f"{API}/links/{vehicle['id']}/confirm", headers=OWNER, json={})
    assert refused.status_code == 409 and refused.json()["code"] == "over_allocated"
    # The owner removes the business allocation and lowers the vehicle part: links and journal stay consistent.
    assert client.delete(f"{API}/allocations/{allocation['id']}", headers=OWNER).status_code == 200
    assert client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_MEMBER).json()["items"] == []
    assert client.post(f"{API}/links/{vehicle['id']}/void", headers=OWNER, json={}).status_code == 200
    assert link(canonical, "120", target_id="veh_car9").status_code == 201


def test_undo_voids_links_and_removes_the_business_row() -> None:
    _s, canonical = fuel_payment()
    message_id = next(m.id for m in sms_memory_store.messages.values() if m.source == "wallet")
    vehicle = link(canonical).json()["link"]
    allocate(canonical)
    assert client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_MEMBER).json()["total"] == 1
    undone = client.post(f"{API}/sms/messages/{message_id}/reverse", headers=OWNER)
    assert undone.status_code == 200, undone.text
    assert {l.status for l in links_memory_store.links.values()} == {"void"}
    assert links_memory_store.links[vehicle["id"]].status_reason == "transaction_undone"
    assert client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_MEMBER).json()["total"] == 0
    refused = link(canonical, "10", target_id="veh_new")
    assert refused.status_code == 409 and refused.json()["code"] == "transaction_undone"


def test_a_business_member_sees_the_allocation_but_never_the_personal_ledger() -> None:
    _s, canonical = fuel_payment()
    allocate(canonical, "90", memo="Client visit")
    page = client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_MEMBER)
    assert page.status_code == 200
    row = page.json()["items"][0]
    assert Decimal(row["amount"]) == 90 and row["currency"] == "SAR" and row["category"] == "fuel" and row["memo"] == "Client visit"
    assert row["source_ref"] == canonical[-6:] and Decimal(page.json()["totals"]["SAR"]) == 90
    text = str(page.json())
    assert "PETROMIN" not in text.upper() and "1234" not in text and "My Visa" not in text and "account" not in row
    # The member's own view of finance in the business workspace has none of the owner's rows.
    assert client.get(f"{API}/ledger/entries", headers=BUSINESS_MEMBER).json()["items"] == []
    assert client.get(f"{API}/links", headers=BUSINESS_MEMBER).json()["items"] == []
    assert client.get(f"{API}/links/transactions/{canonical}", headers=BUSINESS_MEMBER).status_code == 404
    # The owner's own business view shows the same row; another tenant sees nothing.
    assert client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_OWNER_VIEW).json()["total"] == 1
    assert client.get("/api/v1/business/ledger-allocations", headers=OTHER_TENANT).json()["total"] == 0


def test_allocations_only_to_business_workspaces_the_owner_can_write_to() -> None:
    _s, canonical = fuel_payment()
    for workspace in (OTHER_BUSINESS, "ws_not_a_member", WORKSPACE, "../evil"):
        refused = allocate(canonical, "10", workspace=workspace)
        assert refused.status_code in (403, 422), (workspace, refused.text)
    assert allocate(canonical, "10", workspace=OTHER_BUSINESS).json()["code"] == "workspace_not_allowed"
    memory_repository.store.memberships[(TENANT, BUSINESS, "owner_links")].active = False
    assert allocate(canonical, "10").status_code == 403
    # A personal-finance viewer cannot allocate at all.
    assert allocate(canonical, "10", headers={"x-user-roles": "viewer"}).status_code == 403
    # Business links only go through the allocations address.
    direct = client.post(f"{API}/links", headers=OWNER, json={"canonical_id": canonical, "dimension": "business", "role": "payment",
                                                              "target_type": "business_workspace", "target_id": BUSINESS, "amount": "1", "currency": "SAR"})
    assert direct.status_code == 422


def test_links_need_a_registered_module_and_evidence_carries_no_amount() -> None:
    _s, canonical = fuel_payment()
    missing = client.post(f"{API}/links", headers=OWNER, json={"canonical_id": canonical, "dimension": "purchase", "role": "payment",
                                                               "target_type": "purchase", "target_id": "pur_1", "amount": "5", "currency": "SAR"})
    assert missing.status_code == 409 and missing.json()["code"] == "target_unavailable"
    assert link(canonical, "5", target_id="not_a_vehicle").status_code == 404
    K.register_target("document", lambda context, kind, target: True)
    try:
        evidence = client.post(f"{API}/links", headers=OWNER, json={"canonical_id": canonical, "dimension": "document", "role": "evidence",
                                                                    "target_type": "vault_document", "target_id": "doc_1"})
        assert evidence.status_code == 201 and evidence.json()["link"]["amount"] is None
        with_amount = client.post(f"{API}/links", headers=OWNER, json={"canonical_id": canonical, "dimension": "document", "role": "evidence",
                                                                       "target_type": "vault_document", "target_id": "doc_2", "amount": "1", "currency": "SAR"})
        assert with_amount.status_code == 422
    finally:
        K.unregister_target("document")
    assert client.post(f"{API}/links", headers={**OWNER, "x-user-roles": "viewer"}, json={"canonical_id": canonical, "dimension": "vehicle",
                       "role": "payment", "target_type": "vehicle", "target_id": "veh_x", "amount": "1", "currency": "SAR"}).status_code == 403
    assert link("fin_je_missing", "5").status_code == 404


def test_reconciling_a_statement_copy_moves_its_links_to_the_recorded_payment() -> None:
    _s, canonical = fuel_payment()
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_links")
    from anum_api.finance.links_store import FinanceLink, LinkStatus

    now = utc_now()
    links_memory_store.add_link(FinanceLink(id="fin_link_copy", tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id="owner_links",
                                            canonical_id="fin_je_copy", dimension="vehicle", role="payment", target_type="vehicle", target_id="veh_m",
                                            amount=Decimal("20"), currency="SAR", idem_key="x" * 64, created_by="owner_links", created_at=now,
                                            updated_at=now))
    K.settle(finance_store, sms_memory_store, context, [("move", "fin_je_copy", canonical), ("sync", canonical, canonical)])
    moved = links_memory_store.links["fin_link_copy"]
    assert moved.canonical_id == canonical and moved.status == LinkStatus.ACTIVE and moved.status_reason == "moved_on_reconcile"


def test_privacy_export_and_delete_cover_links_allocations_and_device_events() -> None:
    _s, canonical = fuel_payment()
    link(canonical, "50")
    allocate(canonical, "50")
    exported = client.get("/api/v1/privacy/export", headers=OWNER)
    assert exported.status_code == 200, exported.text
    finance = exported.json()["finance"]
    assert len(finance["finance_links"]) == 2 and len(finance["business_allocations_made"]) == 1
    assert "idem_key" not in str(finance["finance_links"])
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_links")
    finance_store.delete_everything(context)
    assert links_memory_store.links == {} and links_memory_store.allocations == {}
    assert client.get("/api/v1/business/ledger-allocations", headers=BUSINESS_MEMBER).json()["total"] == 0


# -- device scopes -----------------------------------------------------------------------------------


@pytest.fixture
def vehicle_integration() -> Iterator[D.Integration]:
    item = D.Integration(key="vehicle.driving", scope="vehicle", permission=D.Permission.FINANCE_MANAGE, ingest_path="/api/v1/vehicles/iphone/drive",
                         per_minute=3, per_day=5, default_on=False, order=30)
    D.register_integration(item)
    yield item
    D._integrations.pop(item.key, None)


def new_device(scopes: list[str], headers: dict = OWNER):  # type: ignore[no-untyped-def]
    return client.post(f"{IPHONE}/devices", headers=headers, json={"name": "Phone", "scopes": scopes})


def switch(key: str, enabled: bool) -> None:
    response = client.put(f"{IPHONE}/integrations/{key}", headers=OWNER, json={"enabled": enabled})
    assert response.status_code == 200, response.text


def test_a_scope_without_a_registered_integration_is_refused() -> None:
    setup()
    refused = new_device(["documents"])
    assert refused.status_code == 404 and refused.json()["code"] == "integration_unavailable"
    assert new_device(["not_a_scope"]).status_code == 422
    with pytest.raises(D.DeviceError) as caught:
        D.authorize_scope("Bearer anything", "documents")
    assert caught.value.code == "integration_unavailable"


def test_scoped_token_checks_scope_switch_pause_revoke_rate_and_role(vehicle_integration: D.Integration) -> None:
    s = setup()
    created = new_device(["vehicle"])
    assert created.status_code == 201, created.text
    token, device = created.json()["token"], created.json()["device"]
    assert "token_hash" not in created.text and device["scopes"] == ["vehicle"]
    with pytest.raises(D.DeviceError) as off:
        D.authorize_scope(f"Bearer {token}", "vehicle")
    assert off.value.code == "disabled" and off.value.status_code == 409
    switch("vehicle.driving", True)
    principal = D.authorize_scope(f"Bearer {token}", "vehicle")
    assert principal.context.user_id == "owner_links" and principal.device_id == device["id"]
    # The finance token cannot reach the vehicle integration, nor the vehicle token the SMS ingest.
    with pytest.raises(D.DeviceError) as scoped:
        D.authorize_scope(f"Bearer {s['token']}", "vehicle")
    assert scoped.value.code == "scope_missing" and scoped.value.status_code == 403
    sms = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": FUEL_SMS, "sender": SENDER})
    assert sms.status_code == 403 and sms.json()["code"] == "scope_missing"
    # Rate limit per device and scope (3 a minute here, refusals included): the refused, the accepted and this one.
    D.authorize_scope(f"Bearer {token}", "vehicle")
    with pytest.raises(D.DeviceError) as limited:
        D.authorize_scope(f"Bearer {token}", "vehicle")
    assert limited.value.status_code == 429 and limited.value.retry_after == 60
    # Pause, resume, rotate (the old token stops), revoke.
    assert client.post(f"{IPHONE}/devices/{device['id']}/pause", headers=OWNER).json()["status"] == "paused"
    links_memory_store.events.clear()
    with pytest.raises(D.DeviceError) as paused:
        D.authorize_scope(f"Bearer {token}", "vehicle")
    assert paused.value.code == "paused"
    assert client.post(f"{IPHONE}/devices/{device['id']}/resume", headers=OWNER).json()["status"] == "active"
    rotated = client.post(f"{IPHONE}/devices/{device['id']}/rotate", headers=OWNER).json()
    with pytest.raises(D.DeviceError) as old:
        D.authorize_scope(f"Bearer {token}", "vehicle")
    assert old.value.code == "invalid_token"
    assert D.authorize_scope(f"Bearer {rotated['token']}", "vehicle").device_id == device["id"]
    assert client.post(f"{IPHONE}/devices/{device['id']}/revoke", headers=OWNER).json()["status"] == "revoked"
    with pytest.raises(D.DeviceError) as revoked:
        D.authorize_scope(f"Bearer {rotated['token']}", "vehicle")
    assert revoked.value.status_code == 401
    # A viewer cannot make tokens for an integration their role cannot use.
    assert new_device(["vehicle"], headers={**OWNER, "x-user-roles": "viewer"}).status_code == 403


def test_iphone_overview_events_test_and_ping(vehicle_integration: D.Integration) -> None:
    setup()
    token = new_device(["vehicle"]).json()["token"]
    overview = client.get(IPHONE, headers=OWNER).json()
    keys = [i["key"] for i in overview["integrations"]]
    assert keys == ["finance.sms", "finance.wallet", "attendance.location", "vehicle.driving"]
    vehicle = next(i for i in overview["integrations"] if i["key"] == "vehicle.driving")
    assert vehicle["enabled"] is False and vehicle["devices_with_scope"] == 1 and vehicle["last_connection_at"] is None
    assert overview["scopes"] == list(D.DEVICE_SCOPES) and all("token" not in d for d in overview["devices"])
    failed = client.post(f"{IPHONE}/ping", headers={"Authorization": f"Bearer {token}"}, json={"integration": "vehicle.driving"})
    assert failed.status_code == 409 and failed.json()["code"] == "disabled"
    test = client.post(f"{IPHONE}/integrations/vehicle.driving/test", headers=OWNER).json()
    assert test["passed"] is False and {"check": "switched_on", "passed": False} in test["checks"]
    switch("vehicle.driving", True)
    ok = client.post(f"{IPHONE}/ping", headers={"Authorization": f"Bearer {token}"}, json={"integration": "vehicle.driving"})
    assert ok.status_code == 200 and ok.json()["status"] == "test_ok"
    assert client.post(f"{IPHONE}/integrations/vehicle.driving/test", headers=OWNER).json()["passed"] is True
    events = client.get(f"{IPHONE}/integrations/vehicle.driving/events", headers=OWNER).json()
    assert events["total"] == 1 and events["items"][0]["code"] == "disabled"
    after = next(i for i in client.get(IPHONE, headers=OWNER).json()["integrations"] if i["key"] == "vehicle.driving")
    assert after["failed_7d"] == 1 and after["last_ok_at"] is not None
    assert client.post(f"{IPHONE}/ping", headers={"Authorization": "Bearer nope"}, json={"integration": "vehicle.driving"}).status_code == 401
    assert client.post(f"{IPHONE}/ping", headers={"Authorization": f"Bearer {token}"}, json={"integration": "unknown"}).status_code == 404
    # Another person in the workspace sees none of the owner's devices or events.
    other = client.get(IPHONE, headers={**OWNER, "x-user-id": "someone_else"}).json()
    assert other["devices"] == [] and all(i["last_connection_at"] is None for i in other["integrations"])


def test_finance_ingest_reports_events_and_the_sms_switch_needs_the_safety_check() -> None:
    s = setup()
    assert send(s["token"], "SYNTHETIC. Purchase of SAR 9.00 at SHOP with card **1234")["status"] == "posted"
    sms = next(i for i in client.get(IPHONE, headers=OWNER).json()["integrations"] if i["key"] == "finance.sms")
    assert sms["enabled"] is True and sms["last_ok_at"] is not None and sms["devices_with_scope"] == 1
    switch("finance.wallet", False)
    off = client.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {s['token']}"},
                      json={"source": "apple_wallet", "amount": "SAR 3", "merchant": "X", "card": "My Visa", "name": "n"})
    assert off.status_code == 409 and off.json()["code"] == "disabled"
    wallet_events = client.get(f"{IPHONE}/integrations/finance.wallet/events", headers=OWNER).json()
    assert [e["code"] for e in wallet_events["items"]] == ["disabled"]
    switch("finance.sms", False)
    assert client.get(f"{API}/sms/settings", headers=OWNER).json()["enabled"] is False


def test_an_attendance_shortcut_signs_in_with_a_scoped_device_token() -> None:
    from anum_api.attendance.store import attendance_store

    attendance_store.clear()
    assert "anum_api.attendance.devices" in D.plug_module_authorities()
    s = setup()
    office = client.post("/api/v1/attendance/sites", headers=OWNER, json={"name": "Head office", "kind": "workplace"}).json()["id"]
    assert client.post("/api/v1/attendance/tracking", headers=OWNER, json={"action": "consent"}).status_code == 200
    created = new_device(["attendance"])
    assert created.status_code == 201, created.text
    bearer = {"Authorization": f"Bearer {created.json()['token']}"}
    body = {"event": "arrive", "site_id": office, "idempotency_key": "scoped-0001"}
    off = client.post("/api/v1/attendance/device/events", headers=bearer, json=body)
    assert off.status_code == 409  # the iPhone page switch is off until the owner turns it on
    switch("attendance.location", True)
    ok = client.post("/api/v1/attendance/device/events", headers=bearer, json=body)
    assert ok.status_code == 201, ok.text
    assert next(iter(attendance_store.events.values())).device_id == created.json()["device"]["id"]
    finance_only = client.post("/api/v1/attendance/device/events", headers={"Authorization": f"Bearer {s['token']}"},
                               json={**body, "idempotency_key": "scoped-0002"})
    assert finance_only.status_code == 403
    attendance = next(i for i in client.get(IPHONE, headers=OWNER).json()["integrations"] if i["key"] == "attendance.location")
    assert attendance["enabled"] is True and attendance["last_ok_at"] is not None and attendance["failed_7d"] == 2
    attendance_store.clear()


def test_scoped_authority_plugs_into_a_module_seam() -> None:
    seam = types.ModuleType("anum_api.attendance.devices")
    received: list[Any] = []
    seam.register_device_authority = received.append  # type: ignore[attr-defined]
    package = types.ModuleType("anum_api.attendance")
    saved = {name: sys.modules.get(name) for name in ("anum_api.attendance", "anum_api.attendance.devices")}
    sys.modules.setdefault("anum_api.attendance", package)
    sys.modules["anum_api.attendance.devices"] = seam
    try:
        assert "anum_api.attendance.devices" in D.plug_module_authorities()
        authority = received[0]
        assert authority.authorize(None, "attendance") is None
        assert authority.authorize("Bearer eyJhbGciOi.session.token", "attendance") is None  # a sign-in token, not a device token
        with pytest.raises(D.DeviceError):
            authority.authorize("Bearer anum_sms.bad", "attendance")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
