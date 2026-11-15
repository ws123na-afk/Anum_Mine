"""Personal Finance isolation invariants under PostgreSQL row-level security (docs/finance.md#privacy).

The safety net the Personal Finance work is checked against: every personal finance table is private to
its owner, in every tenant and workspace, for reads and writes, and the API run as the non-owner
``anum_test_app`` role never shows one person's ledger to another, workspace owners included. The
in-memory twin is ``test_finance_invariants.py``. Every name, merchant and amount is SYNTHETIC.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import main
from anum_api.db import session as db_session
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
OWNER_ID, MEMBER_ID = "user_test", "colleague"
# Both people are members of WORKSPACE_A2; the owner's personal finance is kept there.
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A2, "x-user-id": OWNER_ID, "x-user-roles": "owner"}
MEMBER = {**ME, "x-user-id": MEMBER_ID, "x-user-roles": "member"}
ME_ELSEWHERE = {**ME, "x-workspace-id": WORKSPACE_A}
MARK = "SYNTHPRIVATE"  # appears in every name the owner types, so a leak is easy to find
SMS = "SYNTHETIC. Purchase of SAR 45.50 at SYNTHPRIVATE GROCER with card **1234 on 03/11/2026 14:22. Ref 11AA22"
CSV = "Date,Description,Amount\n2026-11-04,SYNTHPRIVATE BAKERY,-12.00\n2026-11-05,SYNTHPRIVATE SALARY,900.00\n"
# Shared, not personal: a business allocation is visible to the business workspace by design (finance-links.md).
NOT_OWNER_SCOPED = frozenset({"business_ledger_allocations"})
# Investing and trading routes keep no personal ledger rows; their own suites check their isolation.
NOT_LEDGER = ("/intel", "/broker", "/ibkr", "/research", "/trading", "/paper", "/alpaca", "/execution")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        for tenant, workspace, user_id, role in ((TENANT_A, WORKSPACE_A2, OWNER_ID, "owner"), (TENANT_A, WORKSPACE_A2, MEMBER_ID, "member"),
                                                 (TENANT_A, WORKSPACE_A, OWNER_ID, "owner"), (TENANT_B, WORKSPACE_B, OWNER_ID, "owner")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                                    "values (:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "t": tenant, "w": workspace, "r": role})
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    yield TestClient(main.app)
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def _call(api: TestClient, method: str, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
    main.app.state.rate_limiter.reset()  # the sweeps send more requests than one person may in a minute
    return api.request(method, path, headers=headers, **kwargs)


def _made(api: TestClient, path: str, body: dict[str, Any], headers: dict[str, str] = ME) -> dict[str, Any]:
    response = _call(api, "POST", f"{API}{path}", headers, json=body)
    assert response.status_code in (200, 201), (path, response.text)
    return response.json()


def _personal_tables(engine: Engine) -> list[str]:
    """Every table in the finance schema whose rows belong to one person."""
    with engine.connect() as connection:
        rows = connection.execute(text(
            "select c.table_name from information_schema.columns c join information_schema.tables t "
            "on t.table_schema = c.table_schema and t.table_name = c.table_name "
            "where c.table_schema = 'public' and c.column_name = 'owner_user_id' and t.table_type = 'BASE TABLE' "
            "and c.table_name like 'finance%' order by 1")).scalars().all()
    return [r for r in rows if r not in NOT_OWNER_SCOPED]


def _count(app_session: Callable[..., Iterator[Session]], tenant: str, workspace: str, user: str, table: str, owner: str) -> int:
    assert re.fullmatch(r"[a-z_0-9]+", table)
    with app_session(TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        statement = text("select count(*) from " + table + " where owner_user_id = :o")  # nosec B608: the name is from the catalog, checked above
        return int(session.execute(statement, {"o": owner}).scalar_one())


def _populate(api: TestClient) -> set[str]:
    """The owner's ledger touched by every way in: typed, CSV, SMS, Wallet, debts, bills, loans with people, tags, payslips."""
    bank = _made(api, "/accounts", {"name": f"{MARK} Current", "kind": "bank", "currency": "SAR", "last4": "5678"})
    card = _made(api, "/accounts", {"name": f"{MARK} Card", "kind": "card", "currency": "SAR", "last4": "1234"})
    typed = _made(api, "/transactions", {"account_id": bank["id"], "posted_on": "2026-11-03", "amount": "-300",
                                         "description": f"{MARK} LOAN PAYMENT", "category": "transfer"})
    _made(api, "/imports", {"account_id": bank["id"], "content": CSV, "filename": "synthetic.csv"})
    assert _call(api, "POST", f"{API}/sms/self-test", ME).json()["passed"]
    version = _call(api, "GET", f"{API}/sms/settings", ME).json()["version"]
    put = _call(api, "PUT", f"{API}/sms/settings", ME, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "1234", "account_id": card["id"]}]})
    assert put.status_code == 200, put.text
    token = _made(api, "/sms/devices", {"name": f"{MARK} iPhone"})["token"]
    ingested = _call(api, "POST", f"{API}/sms/ingest", {"Authorization": f"Bearer {token}"}, json={"message": SMS, "sender": "TestBank"})
    assert ingested.status_code == 200 and ingested.json()["status"] == "posted", ingested.text
    debt = _made(api, "/debts", {"name": f"{MARK} Loan", "kind": "other", "balance": "1000", "currency": "SAR"})
    entries = _call(api, "GET", f"{API}/ledger/entries", ME).json()["items"]
    canonical = next(e["canonical_id"] for e in entries if e["source_ref"] == typed["id"])
    _made(api, f"/debts/{debt['id']}/payments", {"canonical_id": canonical})
    bill = _made(api, "/bills", {"name": f"{MARK} Rent", "amount": "50", "currency": "SAR", "cadence": "monthly", "next_due_on": "2026-11-20"})
    goal = _made(api, "/goals", {"name": f"{MARK} Goal", "target_amount": "500", "currency": "SAR"})
    loan = _made(api, "/people-loans", {"person": f"{MARK} Friend", "direction": "lent", "amount": "300", "currency": "SAR", "lent_on": "2026-11-01"})
    tag = _made(api, "/tags", {"name": f"{MARK} Trip", "kind": "trip"})
    slip = _made(api, "/payslips", {"employer": f"{MARK} Employer", "month": "2026-11", "gross": "1000", "currency": "SAR"})
    _made(api, "/merchants/aliases", {"alias": f"{MARK} alias", "merchant": f"{MARK} merchant"})
    rule = _made(api, "/rules", {"content": {"match": {"words": [MARK.lower()]}, "category": "groceries"}})
    return {bank["id"], card["id"], typed["id"], debt["id"], bill["id"], goal["id"], loan["id"], tag["id"], slip["id"], rule["rule_id"], canonical}


def test_every_personal_finance_table_has_forced_rls_and_an_owner_policy(database_engine: Engine) -> None:
    tables = _personal_tables(database_engine)
    assert len(tables) >= 30, tables  # the sweep found the finance schema
    weak = []
    with database_engine.connect() as connection:
        for table in tables:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            quals = connection.execute(text("select coalesce(qual, '') || ' ' || coalesce(with_check, '') from pg_policies where tablename = :t"),
                                       {"t": table}).scalars().all()
            if not forced or not any("owner_user_id" in q for q in quals):
                weak.append(table)
    assert weak == [], f"personal finance tables without forced RLS or an owner policy: {weak}"


def test_a_member_and_the_owner_in_other_workspaces_read_and_change_none_of_the_owners_rows(
        client: TestClient, database_engine: Engine, app_session: Callable[..., Iterator[Session]]) -> None:
    _populate(client)
    tables = _personal_tables(database_engine)
    filled = [t for t in tables if _count(app_session, TENANT_A, WORKSPACE_A2, OWNER_ID, t, OWNER_ID)]
    assert {"finance_accounts", "finance_transactions", "finance_journal_entries", "finance_postings", "finance_links"} <= set(filled), filled
    seen = {}
    for table in filled:
        for tenant, workspace, user in ((TENANT_A, WORKSPACE_A2, MEMBER_ID), (TENANT_A, WORKSPACE_A, OWNER_ID), (TENANT_B, WORKSPACE_B, OWNER_ID)):
            found = _count(app_session, tenant, workspace, user, table, OWNER_ID)
            if found:
                seen[(table, workspace, user)] = found
    assert seen == {}, seen
    # Writes: the member's session can neither change nor remove the owner's rows.
    for table in filled:
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A2, user_id=MEMBER_ID), commit=True) as session:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": MEMBER_ID})
            changed = session.execute(text("update " + table + " set owner_user_id = owner_user_id where owner_user_id = :o"),  # nosec B608
                                      {"o": OWNER_ID}).rowcount
            assert changed == 0, table
    for table in filled:
        assert _count(app_session, TENANT_A, WORKSPACE_A2, OWNER_ID, table, OWNER_ID) > 0, table


def test_a_member_cannot_write_a_row_in_the_owners_name(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    with pytest.raises(DBAPIError, match="row-level security"), app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A2, user_id=MEMBER_ID)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": MEMBER_ID})
        session.execute(text(
            "insert into finance_accounts (id, tenant_id, workspace_id, owner_user_id, name, kind, currency, opening_balance, archived, "
            "created_at, updated_at) values ('fin_acct_forged', :t, :w, :o, 'forged', 'bank', 'SAR', 0, false, now(), now())"),
            {"t": TENANT_A, "w": WORKSPACE_A2, "o": OWNER_ID})


def test_no_api_read_shows_the_owners_ledger_to_a_member_or_another_workspace(client: TestClient) -> None:
    ids = _populate(client)
    leaks = []
    for path, operations in sorted(main.app.openapi()["paths"].items()):
        if not path.startswith(API) or "get" not in operations or path.removeprefix(API).startswith(NOT_LEDGER):
            continue
        urls = [path] if "{" not in path else sorted({re.sub(r"{\w+}", i, path) for i in ids})
        for url in urls:
            for who, headers in (("member", MEMBER), ("other workspace", ME_ELSEWHERE)):
                body = _call(client, "GET", url, headers, params={"month": "2026-11"}).text
                if MARK in body or MARK.lower() in body or any(i in body for i in ids):
                    leaks.append((who, url))
    assert leaks == [], leaks
