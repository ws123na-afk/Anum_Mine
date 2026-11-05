"""Finance links, business allocations and scoped device tokens in PostgreSQL (migration ``0134_finance_links``).

The API runs as the non-owner ``anum_test_app`` role. Links and device events are visible only to
their owner; an allocation row is visible to the business workspace it was made for and nowhere
else; the database refuses active links that exceed the payment at commit. Messages are SYNTHETIC.
docs/finance-links.md, docs/iphone-integrations.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A, WORKSPACE_A2
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.authorization import Permission
from anum_api.db import session as db_session
from anum_api.finance import devices as D
from anum_api.finance import links as K
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A2, "x-user-id": "colleague", "x-user-roles": "member"}


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
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                                    "values (:u, :t, :w, :r, true)"), {"u": user_id, "t": TENANT_A, "w": WORKSPACE_A2, "r": role})
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    K.register_target("vehicle", lambda context, kind, target: target.startswith("veh_"))
    yield TestClient(app)
    K.unregister_target("vehicle")
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def _fuel(api: TestClient) -> tuple[str, str]:
    """SAR 150 fuel from Apple Pay and the bank SMS: one canonical transaction. Returns (canonical id, card id)."""
    api.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "last4": "5678"})
    card = api.post(f"{API}/accounts", headers=ME, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert api.post(f"{API}/sms/self-test", headers=ME).json()["passed"]
    version = api.get(f"{API}/sms/settings", headers=ME).json()["version"]
    put = api.put(f"{API}/sms/settings", headers=ME, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "1234", "account_id": card["id"]}], "wallet_cards": [{"card": "My Visa", "account_id": card["id"]}]})
    assert put.status_code == 200, put.text
    token = api.post(f"{API}/sms/devices", headers=ME, json={"name": "iPhone"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    paid = api.post(f"{API}/wallet/ingest", headers=bearer, json={"source": "apple_wallet", "amount": "SAR 150.00", "merchant": "Petromin Fuel",
                                                                 "card": "My Visa", "name": "Purchase"})
    assert paid.json()["status"] == "posted", paid.text
    assert api.post(f"{API}/sms/ingest", headers=bearer, json={"message": FUEL_SMS, "sender": "TestBank"}).json()["status"] == "attached"
    entries = api.get(f"{API}/ledger/entries", headers=ME).json()["items"]
    assert len(entries) == 1
    return entries[0]["canonical_id"], card["id"]


def _rows(app_session: Callable[..., Iterator[Session]], workspace: str, user: str, sql: str) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=workspace, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql)).all())


def _link(api: TestClient, canonical: str, amount: str, target: str = "veh_1"):  # type: ignore[no-untyped-def]
    return api.post(f"{API}/links", headers=ME, json={"canonical_id": canonical, "dimension": "vehicle", "role": "payment",
                                                      "target_type": "vehicle", "target_id": target, "amount": amount, "currency": "SAR"})


def test_link_tables_have_forced_rls_and_the_right_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table, owner in (("finance_links", True), ("finance_device_events", True), ("business_ledger_allocations", False)):
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            expected = {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} | ({f"owner_only_{table}"} if owner else set())
            assert forced and expected <= policies, (table, policies)
            if owner:
                using = connection.execute(text("select qual from pg_policies where tablename = :t and policyname = :p"),
                                           {"t": table, "p": f"owner_only_{table}"}).scalar_one()
                assert "anum_tenant_purger" in using


def test_vehicle_link_and_business_allocation_add_no_posting_and_the_member_sees_only_the_allocation(
        client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    api = client
    canonical, _card = _fuel(api)
    journal_sql = "select e.id, e.status, p.ledger_account, p.amount from finance_journal_entries e join finance_postings p on p.entry_id = e.id order by 1, 3"
    before = _rows(app_session, WORKSPACE_A, "user_test", journal_sql)
    transactions = _rows(app_session, WORKSPACE_A, "user_test", "select count(*) from finance_transactions")
    assert _link(api, canonical, "150").status_code == 201
    allocated = api.post(f"{API}/allocations", headers=ME, json={"canonical_id": canonical, "workspace_id": WORKSPACE_A2, "amount": "150",
                                                                 "currency": "SAR", "category": "fuel"})
    assert allocated.status_code == 201, allocated.text
    assert _rows(app_session, WORKSPACE_A, "user_test", journal_sql) == before
    assert _rows(app_session, WORKSPACE_A, "user_test", "select count(*) from finance_transactions") == transactions
    # The business workspace has the allocation row; its member reads it, never the owner's journal or links.
    assert _rows(app_session, WORKSPACE_A2, "colleague", "select amount, currency, category from business_ledger_allocations") == [
        (Decimal("150.0000"), "SAR", "fuel")]
    page = api.get("/api/v1/business/ledger-allocations", headers=COLLEAGUE).json()
    assert page["total"] == 1 and "PETROMIN" not in str(page).upper()
    for table in ("finance_journal_entries", "finance_postings", "finance_links", "finance_sms_messages"):
        assert _rows(app_session, WORKSPACE_A2, "colleague", f"select count(*) from {table}") == [(0,)]  # nosec B608: fixed names
        assert _rows(app_session, WORKSPACE_A, "colleague", f"select count(*) from {table}") == [(0,)]  # nosec B608: fixed names
    # Over-allocation, then an undo: the links void and the business row goes.
    refused = _link(api, canonical, "1", target="veh_2")
    assert refused.status_code == 409 and refused.json()["code"] == "over_allocated"
    message_id = api.get(f"{API}/sms/messages", headers=ME, params={"status": "posted"}).json()["items"][0]["id"]
    assert api.post(f"{API}/sms/messages/{message_id}/reverse", headers=ME).status_code == 200
    assert {r[0] for r in _rows(app_session, WORKSPACE_A, "user_test", "select status from finance_links")} == {"void"}
    assert _rows(app_session, WORKSPACE_A2, "colleague", "select count(*) from business_ledger_allocations") == [(0,)]


def test_concurrent_links_cannot_exceed_the_payment(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    api = client
    canonical, _card = _fuel(api)
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda i: _link(api, canonical, "100", target=f"veh_{i}").status_code, range(4)))
    assert sorted(codes) == [201, 409, 409, 409]
    assert _rows(app_session, WORKSPACE_A, "user_test", "select sum(amount) from finance_links where status = 'active'") == [(Decimal("100.0000"),)]


def test_the_database_refuses_links_over_the_amount_at_commit(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    canonical, _card = _fuel(client)
    with pytest.raises(DBAPIError, match="exceed the transaction amount"):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test"), commit=True) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            for i in range(2):
                session.execute(text(
                    "insert into finance_links (id, tenant_id, workspace_id, owner_user_id, canonical_id, dimension, role, target_type, target_id, "
                    "amount, currency, status, idem_key, created_by) values (:id, :t, :w, 'user_test', :c, 'vehicle', 'payment', 'vehicle', :target, "
                    "100, 'SAR', 'active', :k, 'user_test')"), {"id": f"l{i}", "t": TENANT_A, "w": WORKSPACE_A, "c": canonical, "target": f"v{i}", "k": f"{i:064d}"})
            session.execute(text("set constraints all immediate"))


def test_a_correction_keeps_links_and_moves_the_allocation_to_review(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    api = client
    canonical, card = _fuel(api)
    _link(api, canonical, "150")
    api.post(f"{API}/allocations", headers=ME, json={"canonical_id": canonical, "workspace_id": WORKSPACE_A2, "amount": "150", "currency": "SAR"})
    message_id = api.get(f"{API}/sms/messages", headers=ME, params={"status": "posted"}).json()["items"][0]["id"]
    corrected = api.post(f"{API}/sms/messages/{message_id}/post", headers=ME, json={"amount": "100.00", "account_id": card})
    assert corrected.status_code == 200 and corrected.json()["canonical_id"] == canonical
    statuses = {r[0] for r in _rows(app_session, WORKSPACE_A, "user_test", "select status from finance_links")}
    assert statuses == {"needs_review"}
    assert _rows(app_session, WORKSPACE_A2, "colleague", "select status from business_ledger_allocations") == [("needs_review",)]


def test_scoped_device_token_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    item = D.Integration(key="vehicle.driving", scope="vehicle", permission=Permission.FINANCE_MANAGE, ingest_path="/x", default_on=True)
    D.register_integration(item)
    try:
        _fuel(client)
        created = client.post("/api/v1/integrations/iphone/devices", headers=ME, json={"name": "Car phone", "scopes": ["vehicle"]})
        assert created.status_code == 201, created.text
        token = created.json()["token"]
        assert D.authorize_scope(f"Bearer {token}", "vehicle").context.user_id == "user_test"
        ping = client.post("/api/v1/integrations/iphone/ping", headers={"Authorization": f"Bearer {token}"}, json={"integration": "finance.sms"})
        assert ping.status_code == 403 and ping.json()["code"] == "scope_missing"
        events = _rows(app_session, WORKSPACE_A, "user_test", "select integration, outcome from finance_device_events order by created_at")
        assert ("vehicle.driving", "ok") in events and ("finance.sms", "denied") in events
        assert _rows(app_session, WORKSPACE_A, "colleague", "select count(*) from finance_device_events") == [(0,)]
        hashes = _rows(app_session, WORKSPACE_A, "user_test", "select token_hash from finance_sms_devices")
        assert all(token.split(".")[-1] not in h[0] for h in hashes)
    finally:
        D._integrations.pop(item.key, None)


def test_deleting_the_owners_personal_workspace_removes_the_allocation_rows_they_made(
        client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace deletion job purges, through the purge role and its policy only (no BYPASSRLS), the rows
    of that workspace and the allocation rows it made in other workspaces; other workspaces' rows stay."""
    from anum_api import tenant_lifecycle as lifecycle

    monkeypatch.setattr(settings, "auth_mode", "headers")
    with database_engine.begin() as connection:
        connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values ('admin', :t, :w, 'owner', true)"),
                           {"t": TENANT_A, "w": WORKSPACE_A})
        # WORKSPACE_A2 is the owner's personal workspace; WORKSPACE_A is the business.
        for row_id, source in (("alloc_from_personal", WORKSPACE_A2), ("alloc_from_elsewhere", "workspace_other_source")):
            connection.execute(text(
                "insert into business_ledger_allocations (id, tenant_id, workspace_id, source_link_id, source_workspace_id, allocated_by, source_ref, "
                "posted_on, amount, currency, category, treatment, status) values (:id, :t, :w, :link, :src, 'admin', 'abc123', current_date, 10, "
                "'SAR', 'fuel', 'owner_contribution', 'active')"), {"id": row_id, "t": TENANT_A, "w": WORKSPACE_A, "link": f"link_{row_id}", "src": source})
    owner = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A2, "x-user-id": "user_test", "x-user-roles": "owner,org_admin"}
    asked = client.post("/api/v1/workspaces/current/deletion", headers=owner, json={"confirm_name": "Workspace A2"})
    assert asked.status_code == 202, asked.text
    with database_engine.begin() as connection:
        connection.execute(text("update tenant_lifecycle_jobs set purge_after = now() - interval '1 second' where status = 'scheduled'"))
    assert lifecycle.run_lifecycle_pass() == {"purged": 1}
    with database_engine.begin() as connection:
        left = connection.execute(text("select id from business_ledger_allocations where tenant_id = :t order by id"), {"t": TENANT_A}).scalars().all()
        bypass = connection.execute(text("select rolbypassrls from pg_roles where rolname = 'anum_tenant_purger'")).scalar_one()
    assert left == ["alloc_from_elsewhere"] and bypass is False
