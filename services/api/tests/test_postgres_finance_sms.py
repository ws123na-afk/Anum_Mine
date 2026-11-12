"""iPhone SMS and the journal in PostgreSQL (migration ``0131_finance_sms_ledger``, docs/finance-sms.md).

The API runs as the non-owner ``anum_test_app`` role. Every row is visible only to its owner in its
tenant and workspace; the database refuses an unbalanced entry at commit, any change to a posting
and kept text on a refused message. Messages are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import sms_service as svc
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
TABLES = ("finance_sms_settings", "finance_sms_devices", "finance_sms_messages", "finance_sms_rules", "finance_journal_entries", "finance_postings")
PURCHASE = "SYNTHETIC. Purchase of SAR 45.50 at CARREFOUR RIYADH with card **1234 on 03/11/2026 14:22. Ref 88AB12"
SALARY = "SYNTHETIC. Salary of SAR 12,000.00 credited to your account ***5678 on 01/11/2026. Available balance SAR 14,000.00"


def headers(user_id: str = "user_test", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
SOMEONE = headers("user_owner")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    finance_store.clear()
    sms_memory_store.clear()
    svc._last_notified.clear()
    yield TestClient(app)
    finance_store.clear()
    sms_memory_store.clear()


def _count(app_session: Callable[..., Iterator[Session]], table: str, user_id: str) -> int:
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608 - fixed table names


def _setup(api: TestClient) -> dict[str, str]:
    bank = api.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "last4": "5678"}).json()
    card = api.post(f"{API}/accounts", headers=ME, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert api.post(f"{API}/sms/self-test", headers=ME).json()["passed"]
    version = api.get(f"{API}/sms/settings", headers=ME).json()["version"]
    put = api.put(f"{API}/sms/settings", headers=ME, json={"version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}]})
    assert put.status_code == 200, put.text
    device = api.post(f"{API}/sms/devices", headers=ME, json={"name": "iPhone"})
    assert device.status_code == 201, device.text
    return {"bank": bank["id"], "card": card["id"], "token": device.json()["token"]}


def _send(api: TestClient, token: str, message: str) -> dict:
    response = api.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": "TestBank"})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_sms_table_has_forced_rls_an_owner_policy_and_a_purge_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies


def test_sms_to_journal_to_dashboard_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # A retry is recognised within the same clock minute (``idem_key``). Hold the clock still so the two
    # sends below cannot straddle a minute boundary on a slow runner.
    fixed = svc.utc_now().replace(second=5, microsecond=0)
    monkeypatch.setattr(svc, "utc_now", lambda: fixed)
    api = client
    s = _setup(api)
    first = _send(api, s["token"], SALARY)
    assert first["status"] == "review" and first["reason"] == "format_unverified"
    assert api.post(f"{API}/sms/messages/{first['message_id']}/post", headers=ME, json={"confirm_format": True}).status_code == 200
    assert _send(api, s["token"], PURCHASE)["status"] == "posted"
    assert _send(api, s["token"], PURCHASE)["replayed"] is True
    assert _send(api, s["token"], PURCHASE.replace("SYNTHETIC.", "SYNTHETIC copy."))["status"] == "duplicate"
    board = api.get(f"{API}/ledger/dashboard", headers=ME, params={"month": "2026-11"}).json()
    assert board["income"] == [{"currency": "SAR", "amount": "12000.00"}] and board["expenses"] == [{"currency": "SAR", "amount": "45.50"}]
    assert next(c for c in board["cards"] if c["id"] == s["card"])["owed"] == "45.50"
    for table in ("finance_sms_devices", "finance_sms_messages", "finance_journal_entries", "finance_postings"):
        assert _count(app_session, table, "user_test") >= 1 and _count(app_session, table, "user_owner") == 0
    assert api.get(f"{API}/sms/messages", headers=SOMEONE).json()["total"] == 0
    assert api.get(f"{API}/ledger/dashboard", headers=SOMEONE, params={"month": "2026-11"}).json()["entry_count"] == 0
    # Correcting reverses: the journal still balances and nothing was updated in place.
    message = api.get(f"{API}/sms/messages", headers=ME, params={"status": "posted"}).json()["items"][0]
    assert api.post(f"{API}/sms/messages/{message['id']}/post", headers=ME, json={"amount": "40.00"}).status_code == 200
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        unbalanced = session.execute(text(
            "select entry_id from finance_postings group by entry_id, currency having sum(amount) <> 0")).all()
        assert unbalanced == []
        token_rows = session.execute(text("select token_hash from finance_sms_devices")).scalars().all()
        assert all(len(h) == 64 and h not in s["token"] for h in token_rows)
    # Delete my data removes every SMS and journal row of the person.
    ctx = tenant_context()
    from anum_api.finance.store import open_finance_store

    with open_finance_store(ctx) as store:
        store.delete_everything(ctx)
    for table in TABLES:
        assert _count(app_session, table, "user_test") == 0


def test_database_refuses_unbalanced_entries_posting_edits_and_kept_refused_text(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    common = {"t": TENANT_A, "w": WORKSPACE_A, "u": "user_test"}
    entry = ("insert into finance_journal_entries (id, tenant_id, workspace_id, owner_user_id, posted_on, kind, description, source, status) "
             "values (:id, :t, :w, :u, '2026-11-01', 'card_purchase', 'x', 'sms', 'posted')")
    posting = ("insert into finance_postings (id, tenant_id, workspace_id, owner_user_id, entry_id, ledger_account, amount, currency) "
               "values (:id, :t, :w, :u, :e, :a, :amt, 'SAR')")
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(entry), {**common, "id": "je_bad"})
            session.execute(text(posting), {**common, "id": "p1", "e": "je_bad", "a": "expense:groceries", "amt": Decimal("10")})
            session.execute(text(posting), {**common, "id": "p2", "e": "je_bad", "a": "asset:x", "amt": Decimal("-9")})
            session.execute(text("set constraints all immediate"))  # what commit does
    with app_session(tenant_context(), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        session.execute(text(entry), {**common, "id": "je_ok"})
        session.execute(text(posting), {**common, "id": "p3", "e": "je_ok", "a": "expense:groceries", "amt": Decimal("10")})
        session.execute(text(posting), {**common, "id": "p4", "e": "je_ok", "a": "asset:x", "amt": Decimal("-10")})
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            updated = session.execute(text("update finance_postings set amount = 11 where id = 'p3'"))
            assert updated.rowcount == 0  # never reached: the trigger refuses first
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(
                "insert into finance_sms_messages (id, tenant_id, workspace_id, owner_user_id, source, idem_key, received_at, sender, body_masked, "
                "body_purge_at, status) values ('m1', :t, :w, :u, 'device', 'k', now(), 's', 'kept text', now(), 'rejected')"), common)


WALLET_SMS = "SYNTHETIC. Purchase of SAR 125.00 at PANDA HYPERMARKET with card **1234"


def _wallet_setup(api: TestClient) -> dict[str, str]:
    s = _setup(api)
    version = api.get(f"{API}/sms/settings", headers=ME).json()["version"]
    put = api.put(f"{API}/sms/settings", headers=ME, json={"version": version, "verified_senders": ["TestBank"],
                                                            "wallet_cards": [{"card": "My Visa", "account_id": s["card"]}]})
    assert put.status_code == 200, put.text
    return s


def _wallet(api: TestClient, token: str, merchant: str = "Panda Hypermarket") -> dict:
    response = api.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {token}"},
                        json={"source": "apple_wallet", "amount": "SAR 125.00", "merchant": merchant, "card": "My Visa", "name": "Purchase"})
    assert response.status_code == 200, response.text
    return response.json()


def _expense_rows(app_session: Callable[..., Iterator[Session]]) -> list[Decimal]:
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        return list(session.execute(text("select amount from finance_postings where ledger_account like 'expense:%'")).scalars())


@pytest.mark.parametrize("order", ["wallet_first", "sms_first", "concurrent"])
def test_apple_pay_and_sms_are_one_expense_under_rls(order: str, client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    api = client
    s = _wallet_setup(api)
    if order == "wallet_first":
        statuses = [_wallet(api, s["token"])["status"], _send(api, s["token"], WALLET_SMS)["status"]]
    elif order == "sms_first":
        statuses = [_send(api, s["token"], WALLET_SMS)["status"], _wallet(api, s["token"])["status"]]
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_wallet, api, s["token"]), pool.submit(_send, api, s["token"], WALLET_SMS)]
            statuses = [f.result()["status"] for f in futures]
    assert sorted(statuses) == ["attached", "posted"]
    assert _expense_rows(app_session) == [Decimal("125.0000")]
    board = api.get(f"{API}/ledger/dashboard", headers=ME, params={"month": api.get(f"{API}/ledger/entries", headers=ME).json()["items"][0]["posted_on"][:7]}).json()
    assert board["expenses"] == [{"currency": "SAR", "amount": "125.00"}]
    # A second genuine purchase of the same amount elsewhere stays separate.
    assert _wallet(api, s["token"], merchant="Jarir Bookstore")["status"] == "posted"
    assert len(_expense_rows(app_session)) == 2


def test_database_refuses_two_wallet_sources_on_one_transaction(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    api = client
    s = _wallet_setup(api)
    first = _wallet(api, s["token"])
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            entry = session.execute(text("select entry_id from finance_sms_messages where id = :id"), {"id": first["message_id"]}).scalar_one()
            session.execute(text(
                "insert into finance_sms_messages (id, tenant_id, workspace_id, owner_user_id, source, idem_key, received_at, sender, status, entry_id) "
                "values ('m_copy', :t, :w, 'user_test', 'wallet', 'k2', now(), 'applewallet', 'attached', :e)"), {"t": TENANT_A, "w": WORKSPACE_A, "e": entry})


def test_a_hand_entered_transaction_edited_twice_then_deleted_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    api = client
    bank = api.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR"}).json()["id"]
    txn = api.post(f"{API}/transactions", headers=ME, json={"account_id": bank, "posted_on": "2026-11-04", "amount": "-20.00", "description": "CASH TAXI"}).json()
    assert api.patch(f"{API}/transactions/{txn['id']}", headers=ME, json={"amount": "-25.00"}).status_code == 200
    assert api.patch(f"{API}/transactions/{txn['id']}", headers=ME, json={"amount": "-30.00"}).status_code == 200
    expenses = api.get(f"{API}/ledger/dashboard", headers=ME, params={"month": "2026-11"}).json()["expenses"]
    assert expenses == [{"currency": "SAR", "amount": "30.00"}]
    assert api.delete(f"{API}/transactions/{txn['id']}", headers=ME).status_code == 204
    assert api.get(f"{API}/ledger/dashboard", headers=ME, params={"month": "2026-11"}).json()["expenses"] == []
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        assert session.execute(text("select count(*) from finance_journal_entries where status = 'posted' and source <> 'reversal'")).scalar_one() == 0
        assert session.execute(text("select coalesce(sum(amount), 0) from finance_postings where ledger_account like 'expense:%'")).scalar_one() == 0


def test_repeated_and_undone_repayments_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    api = client
    s = _setup(api)
    version = api.get(f"{API}/sms/settings", headers=ME).json()["version"]
    api.put(f"{API}/sms/settings", headers=ME, json={"version": version, "verified_senders": ["TestBank"]})
    card_side = _send(api, s["token"], "SYNTHETIC. Card payment received SAR 500.00 on card ending 1234 on 05/11/2026")
    bank_side = "SYNTHETIC. Credit card payment of SAR 500.00 from account ***5678 to card ***1234 on 05/11/2026"
    assert _send(api, s["token"], bank_side)["status"] == "duplicate"
    assert api.post(f"{API}/sms/paste", headers=ME, json={"sender": "TestBank", "message": bank_side}).json()["status"] == "duplicate"
    balance = lambda: next(a for a in api.get(f"{API}/accounts", headers=ME).json() if a["id"] == s["bank"])["balance"]  # noqa: E731
    assert balance() == "-500.00"
    assert api.post(f"{API}/sms/messages/{card_side['message_id']}/reverse", headers=ME).status_code == 200
    assert balance() == "0.00"
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        assert session.execute(text("select coalesce(sum(amount), 0) from finance_postings where ledger_account = 'suspense:own_transfer'")).scalar_one() == 0


def test_dashboard_summary_and_safe_to_spend_with_debts_under_rls(client: TestClient) -> None:
    """A debt on the ledger dashboard (it used to fail), and net worth counting a card kept as an account and a debt once."""
    api = client
    s = _setup(api)
    linked = api.post(f"{API}/debts", headers=ME, json={"name": "Card", "kind": "card", "balance": "500", "currency": "SAR", "apr_percent": "30",
                                                      "minimum_payment": "50", "account_id": s["card"]})
    loose = api.post(f"{API}/debts", headers=ME, json={"name": "Loan", "kind": "loan", "balance": "1000", "currency": "SAR", "apr_percent": "5",
                                                     "minimum_payment": "100"})
    assert linked.status_code == 201 and loose.status_code == 201, (linked.text, loose.text)
    board = api.get(f"{API}/ledger/dashboard", headers=ME, params={"month": "2026-11"})
    assert board.status_code == 200, board.text
    assert next(d for d in board.json()["debts"] if d["name"] == "Loan")["months_to_clear"] == 11
    summary = api.get(f"{API}/summary", headers=ME, params={"month": "2026-11"}).json()
    balances = Decimal(next((r["amount"] for r in summary["balances"] if r["currency"] == "SAR"), "0"))
    assert summary["unlinked_debts"] == [{"currency": "SAR", "amount": "1000.00"}]
    assert Decimal(summary["net_worth"][0]["amount"]) == balances - Decimal("1000.00")
    assert api.get(f"{API}/safe-to-spend", headers=ME).status_code == 200
    assert api.get(f"{API}/ledger/dashboard", headers=SOMEONE, params={"month": "2026-11"}).json()["debts"] == []


def test_a_loan_repayment_keeps_both_loan_rows_under_rls(client: TestClient) -> None:
    """A loan repayment posts two rows on the loan (the transfer and the interest): the unique key keeps both."""
    api = client
    s = _setup(api)
    loan = api.post(f"{API}/accounts", headers=ME, json={"name": "Car loan", "kind": "loan", "currency": "SAR", "opening_balance": "-10000"}).json()
    review = _send(api, s["token"], "SYNTHETIC. Your account ***5678 was debited.")
    assert review["status"] == "review"
    posted = api.post(f"{API}/sms/messages/{review['message_id']}/post", headers=ME, json={
        "kind": "loan_repayment", "amount": "2000", "currency": "SAR", "account_id": s["bank"], "counter_account_id": loan["id"],
        "interest": "300", "occurred_on": "2026-11-04"})
    assert posted.status_code == 200, posted.text
    accounts = {a["id"]: a for a in api.get(f"{API}/accounts", headers=ME).json()}
    assert accounts[loan["id"]]["balance"] == "-8300.00" and accounts[s["bank"]]["balance"] == "-2000.00"
    summary = api.get(f"{API}/summary", headers=ME, params={"month": "2026-11"}).json()
    assert summary["spend"] == [{"currency": "SAR", "amount": "300.00"}]



def test_learning_from_reviews_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    """docs/finance-sms.md#learning: two readings recorded unchanged trust the sender; the same category twice makes an
    approved learned finance rule; both are the person's own and undo works, under RLS as the app role."""
    api = client
    s = _setup(api)

    def purchase(amount: str, day: int) -> str:
        return f"SYNTHETIC. Purchase of SAR {amount} at ZEBRA MART with card **1234 on {day:02d}/11/2026 14:22"

    learned: list[dict] = []
    for amount, day in (("20.00", 1), ("21.00", 2)):
        waiting = _send(api, s["token"], purchase(amount, day))
        assert waiting["reason"] == "format_unverified"
        recorded = api.post(f"{API}/sms/messages/{waiting['message_id']}/post", headers=ME, json={"category": "health"})
        assert recorded.status_code == 200, recorded.text
        learned += recorded.json()["learned"]
    assert sorted(n["kind"] for n in learned) == ["category_remembered", "sender_trusted"]
    third = _send(api, s["token"], purchase("22.00", 3))
    assert third["status"] == "posted"
    shown = api.get(f"{API}/sms/messages/{third['message_id']}", headers=ME).json()
    assert shown["parsed"]["learned"]["value"] == "health" and shown["fix"]["action"] == "undo_learning"
    assert api.get(f"{API}/sms/learning", headers=SOMEONE).json()["senders"] == []
    assert _count(app_session, "finance_rules", "user_test") >= 1 and _count(app_session, "finance_rules", "user_owner") == 0
    undo = api.post(f"{API}/sms/messages/{third['message_id']}/undo-learning", headers=ME)
    assert undo.status_code == 200, undo.text
    assert undo.json()["reason"] == "learning_undone" and undo.json()["learning"]["categories"] == []
    untrust = api.post(f"{API}/sms/learning/senders/untrust", headers=ME, json={"sender": "TestBank"})
    assert untrust.status_code == 200, untrust.text
    assert _send(api, s["token"], purchase("23.00", 4))["reason"] == "format_unverified"
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        unbalanced = session.execute(text(
            "select entry_id from finance_postings group by entry_id, currency having sum(amount) <> 0")).all()
        assert unbalanced == []
