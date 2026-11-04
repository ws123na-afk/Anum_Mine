"""Purchases in PostgreSQL (migration ``0136_purchases``, docs/purchases.md#storage).

The API runs as the non-owner ``anum_test_app`` role. A personal purchase and its rows are visible
only to their owner; a business purchase to the workspace; the history refuses every update; a
linked payment or refund adds no posting and its read copy follows corrections and undo in the
same transaction; concurrent refunds can never add up to more than was paid. Bank and shared
messages are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.purchases import devices as PD
from anum_api.purchases import jobs as J
from anum_api.purchases.service import plug_finance_links
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/purchases"
FIN = "/api/v1/finance"
PURCHASE_SMS = "SYNTHETIC. Purchase of SAR 450.00 at AMAZON SA with card **1234"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("purchases", "purchase_items", "purchase_events", "purchase_shipments", "purchase_returns", "purchase_reminders", "purchase_preferences")
JOURNAL = "select e.id, e.status, p.ledger_account, p.amount from finance_journal_entries e join finance_postings p on p.entry_id = e.id order by 1, 3"


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        for user_id, role in (("user_test", "owner"), ("colleague", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()
    svc._last_notified.clear()
    plug_finance_links()
    PD.register()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()


def _rows(app_session: Callable[..., Iterator[Session]], user: str, sql: str, params: dict | None = None) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql), params or {}).all())


def _purchase(api: TestClient, headers: dict = ME, **body: object) -> dict:
    payload = {"merchant": "Amazon", "order_ref": "AMZ-1", "ordered_on": utc_now().date().isoformat(), "currency": "SAR", "total": "450",
               "items": [{"description": "Headphones", "quantity": "1", "unit_price": "300"}, {"description": "Cable", "quantity": "2", "unit_price": "75"}],
               **body}
    response = api.post(API, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _delivered(api: TestClient, headers: dict = ME, **body: object) -> dict:
    created = _purchase(api, headers, **body)
    moved = api.post(f"{API}/{created['id']}/status", headers=headers, json={"to": "delivered", "version": created["version"]})
    assert moved.status_code == 200, moved.text
    return moved.json()


def _finance(api: TestClient) -> tuple[str, str, dict]:
    card = api.post(f"{FIN}/accounts", headers=ME, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert api.post(f"{FIN}/sms/self-test", headers=ME).json()["passed"]
    version = api.get(f"{FIN}/sms/settings", headers=ME).json()["version"]
    assert api.put(f"{FIN}/sms/settings", headers=ME, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "1234", "account_id": card["id"]}], "wallet_cards": [{"card": "My Visa", "account_id": card["id"]}]}).status_code == 200
    token = api.post(f"{FIN}/sms/devices", headers=ME, json={"name": "iPhone"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert api.post(f"{FIN}/wallet/ingest", headers=bearer, json={"source": "apple_wallet", "amount": "SAR 450.00", "merchant": "Amazon SA",
                                                                  "card": "My Visa", "name": "Purchase"}).json()["status"] == "posted"
    assert api.post(f"{FIN}/sms/ingest", headers=bearer, json={"message": PURCHASE_SMS, "sender": "TestBank"}).json()["status"] == "attached"
    entries = api.get(f"{FIN}/ledger/entries", headers=ME).json()["items"]
    assert len(entries) == 1
    return entries[0]["canonical_id"], card["id"], bearer


def _refund(api: TestClient, app_session: Callable[..., Iterator[Session]], bearer: dict, amount: str) -> str:
    sent = api.post(f"{FIN}/sms/ingest", headers=bearer, json={"message": f"SYNTHETIC. Refund of SAR {amount} from AMAZON SA to card **1234",
                                                                "sender": "TestBank"})
    assert sent.json()["status"] == "posted", sent.text
    rows = _rows(app_session, "user_test", "select canonical_id from finance_journal_entries where kind = 'refund' and status = 'posted' "
                                         "order by created_at desc limit 1")
    return rows[0][0]


def test_purchase_tables_have_forced_rls_and_visibility_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}", f"purchase_visible_{table}",
                               f"purchase_insert_{table}"} <= policies, (table, policies)


def test_personal_purchases_are_private_business_ones_shared_and_history_append_only(client: TestClient,
                                                                                     app_session: Callable[..., Iterator[Session]]) -> None:
    mine = _purchase(client)
    shared = _purchase(client, COLLEAGUE, ownership="business", order_ref="BIZ-1")
    assert client.post(f"{API}/{shared['id']}/status", headers=ME, json={"to": "shipped", "version": shared["version"]}).status_code == 200
    assert {r[0] for r in _rows(app_session, "colleague", "select id from purchases")} == {shared["id"]}
    assert _rows(app_session, "colleague", "select count(*) from purchase_items where purchase_id = :p", {"p": mine["id"]}) == [(0,)]
    assert _rows(app_session, "colleague", "select count(*) from purchase_events where purchase_id = :p", {"p": shared["id"]}) == [(2,)]
    assert client.get(f"{API}/{mine['id']}", headers=COLLEAGUE).status_code == 404
    # A colleague cannot write a row that claims another person's personal purchase.
    with pytest.raises(DBAPIError):
        _rows(app_session, "colleague",
              "insert into purchase_events (id, tenant_id, workspace_id, owner_user_id, purchase_id, visibility, purchase_owner_id, kind, occurred_at) "
              "values ('x', :t, :w, 'colleague', :p, 'business', 'user_test', 'note', now())", {"t": TENANT_A, "w": WORKSPACE_A, "p": mine["id"]})
    with pytest.raises(DBAPIError, match="append-only"):
        _rows(app_session, "user_test", "update purchase_events set note = 'rewritten' where purchase_id = :p", {"p": mine["id"]})
    with pytest.raises(DBAPIError, match="keeps its ownership"):
        _rows(app_session, "user_test", "update purchases set ownership = 'business' where id = :p", {"p": mine["id"]})


def test_payment_and_partial_refund_add_no_posting_and_follow_undo(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    canonical, card, bearer = _finance(client)
    purchase_message = client.get(f"{FIN}/sms/messages", headers=ME, params={"status": "posted"}).json()["items"][0]["id"]
    created = _delivered(client)
    before = _rows(app_session, "user_test", JOURNAL)
    transactions = _rows(app_session, "user_test", "select count(*) from finance_transactions")
    assert client.post(f"{API}/{created['id']}/payment", headers=ME, json={"canonical_id": canonical}).status_code == 201
    assert client.post(f"{API}/{created['id']}/payment", headers=ME, json={"canonical_id": canonical}).status_code == 200
    assert _rows(app_session, "user_test", JOURNAL) == before
    assert _rows(app_session, "user_test", "select count(*) from finance_transactions") == transactions
    cable = next(i for i in created["items"] if i["description"] == "Cable")
    ret = client.post(f"{API}/{created['id']}/returns", headers=ME, json={"reason": "wrong_item", "items": [{"item_id": cable["id"], "quantity": 2}]}).json()
    refund = _refund(client, app_session, bearer, "150.00")
    with_refund = _rows(app_session, "user_test", JOURNAL)
    linked = client.post(f"{API}/returns/{ret['id']}/refunds", headers=ME, json={"canonical_id": refund})
    assert linked.status_code == 201, linked.text
    assert _rows(app_session, "user_test", JOURNAL) == with_refund
    assert _rows(app_session, "user_test", "select dimension, role, target_type from finance_links where canonical_id = :c", {"c": refund}) == [
        ("purchase", "refund", "purchase_return")]
    assert _rows(app_session, "user_test", "select status, refunds->0->>'amount', refunds->0->>'original_canonical_id' from purchase_returns") == [
        ("refunded", "150.00", canonical)]
    assert _rows(app_session, "user_test", "select status from purchases where id = :p", {"p": created["id"]}) == [("partially_refunded",)]
    # The refund lowered spending: no income posting anywhere.
    assert _rows(app_session, "user_test", "select count(*) from finance_postings where ledger_account like 'income:%'") == [(0,)]
    # A colleague reads nothing of the payer's ledger or links.
    assert _rows(app_session, "colleague", "select count(*) from finance_links") == [(0,)]
    # Corrected with the same figures: the payment link stays; undone: the refund is void and the return waits again.
    assert client.post(f"{FIN}/sms/messages/{purchase_message}/post", headers=ME, json={"amount": "450.00", "account_id": card}).status_code == 200
    assert _rows(app_session, "user_test", "select payment->>'status', payment->>'canonical_id' from purchases where id = :p", {"p": created["id"]}) == [
        ("active", canonical)]
    posted = client.get(f"{FIN}/sms/messages", headers=ME, params={"status": "posted"}).json()["items"]
    refund_message = next(m["id"] for m in posted if m["id"] != purchase_message)
    assert client.post(f"{FIN}/sms/messages/{refund_message}/reverse", headers=ME).status_code == 200
    assert _rows(app_session, "user_test", "select status, refunds->0->>'status' from purchase_returns") == [("refund_pending", "void")]
    assert _rows(app_session, "user_test", "select status from purchases where id = :p", {"p": created["id"]}) == [("refund_pending",)]


def test_concurrent_refunds_never_add_up_to_more_than_was_paid(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    canonical, _card, bearer = _finance(client)
    created = _delivered(client)
    assert client.post(f"{API}/{created['id']}/payment", headers=ME, json={"canonical_id": canonical, "amount": "200"}).status_code == 201
    first = client.post(f"{API}/{created['id']}/returns", headers=ME, json={"reason": "damaged", "amount": "150"}).json()
    second = client.post(f"{API}/{created['id']}/returns", headers=ME, json={"reason": "damaged", "amount": "120"}).json()
    one = _refund(client, app_session, bearer, "150.00")
    two = _refund(client, app_session, bearer, "120.00")
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda pair: client.post(f"{API}/returns/{pair[0]}/refunds", headers=ME,
                                                       json={"canonical_id": pair[1], "amount": pair[2]}).status_code,
                              ((first["id"], one, "150"), (second["id"], two, "120"))))
    assert sorted(codes) == [201, 409], codes
    total = _rows(app_session, "user_test", "select coalesce(sum(amount), 0) from finance_links where role = 'refund' and status = 'active'")[0][0]
    assert total <= 200
    stored = _rows(app_session, "user_test", "select count(*) from purchase_returns where jsonb_array_length(refunds) > 0")
    assert stored == [(1,)]


def test_cash_is_one_finance_transaction_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    cash = client.post(f"{FIN}/accounts", headers=ME, json={"name": "Wallet", "kind": "cash", "currency": "SAR"}).json()
    created = _purchase(client, total="35", items=[])
    body = {"account_id": cash["id"], "amount": "35", "posted_on": utc_now().date().isoformat()}
    for expected in (201, 200):
        assert client.post(f"{API}/{created['id']}/payment/cash", headers={**ME, "Idempotency-Key": "k1"}, json=body).status_code == expected
    assert _rows(app_session, "user_test", "select count(*) from finance_transactions") == [(1,)]
    assert _rows(app_session, "user_test", "select count(*) from finance_journal_entries") == [(1,)]
    assert _rows(app_session, "user_test", "select payment->>'recorded_as_cash' from purchases") == [("true",)]


def test_shared_messages_never_reach_finance_and_reminders_and_privacy(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    shared = client.post(PD.INGEST_PATH, headers=ME, json={"text": "SYNTHETIC. Your Amazon order #406-1234567-7654321 has shipped. Tracking number 31234567890."})
    assert shared.status_code == 200 and shared.json()["purchase_status"] == "shipped", shared.text
    assert _rows(app_session, "user_test", "select count(*) from finance_sms_messages") == [(0,)]
    assert _rows(app_session, "user_test", "select count(*) from finance_journal_entries") == [(0,)]
    late = (utc_now().date() - timedelta(days=2)).isoformat()
    _purchase(client, order_ref="LATE-1", expected_delivery_on=late, ordered_on=(utc_now().date() - timedelta(days=5)).isoformat())
    business = _purchase(client, COLLEAGUE, ownership="business", order_ref="BIZ-7")
    assert (TENANT_A, WORKSPACE_A, "user_test") in J.discover_people()
    first = J.run()
    assert first["reminders"] >= 2, first
    assert J.run()["reminders"] == 0
    assert _rows(app_session, "user_test", "select count(*) from user_notifications where kind = 'purchase_reminder'")[0][0] >= 2
    assert client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"}).status_code == 200
    assert {r[0] for r in _rows(app_session, "colleague", "select id from purchases")} == {business["id"]}
    assert _rows(app_session, "user_test", "select count(*) from purchases where owner_user_id = 'user_test'") == [(0,)]
