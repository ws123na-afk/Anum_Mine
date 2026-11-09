"""Business tax in PostgreSQL (migration ``0154_business_tax``, docs/business-tax.md).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. The e-invoice counter never repeats when
generations race, the database refuses a counter that jumps and any change to generated e-invoice data, journal
lines keep their tax code, and other businesses read nothing. Sellers, buyers and numbers are SYNTHETIC.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from conftest import TENANT_A, WORKSPACE_A, WORKSPACE_A2
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_postgres_business_ledger import ME, _rows, _setup, client  # noqa: F401  (``client`` is the PostgreSQL API fixture)

from anum_api.business import zatca as Z
from anum_api.business.invoice_models import Invoice, InvoiceLine
from anum_api.business.models import Account, BillingProfile, LineKind
from anum_api.business.store import open_business_store
from anum_api.schemas import TenantContext, utc_now

pytestmark = pytest.mark.database
API = "/api/v1/business/books"
TAX = f"{API}/tax"
TABLES = ("business_tax_settings", "business_tax_rates", "business_zatca_chains", "business_zatca_documents")
SA_NUMBER = "300000000000003"
CONTEXT = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A2, user_id="user_test")


def _tax(api: TestClient, **body: object) -> dict:
    response = api.put(f"{TAX}/settings", headers=ME, json={"jurisdiction": "SA", "registered": True, "registration_number": SA_NUMBER,
                                                           "version": None, **body})
    assert response.status_code == 200, response.text
    return response.json()


def _invoices(count: int) -> list[str]:
    now = utc_now()
    ids = []
    with open_business_store(CONTEXT) as store:
        store.add("accounts", Account(id="acc_synthetic", tenant_id=TENANT_A, workspace_id=WORKSPACE_A2, created_at=now, updated_at=now,
                                      name="Synthetic Buyer Co", name_key="synthetic buyer co", owner_id="user_test", created_by="user_test"))
        for index in range(count):
            line = InvoiceLine(description="Synthetic service", kind=LineKind.SERVICE, unit_price=Decimal(100), net=Decimal(100), tax=Decimal(15),
                               total=Decimal(115), tax_rate_pct=Decimal(15), tax_code="standard")
            invoice = Invoice(id=f"binv_tax_{index}", tenant_id=TENANT_A, workspace_id=WORKSPACE_A2, created_at=now, updated_at=now,
                              account_id="acc_synthetic", status="sent", currency="SAR", lines=[line], subtotal=Decimal(100), tax=Decimal(15),
                              total=Decimal(115), seq=index + 1, number=f"INV-2026-{index + 1:04d}", issue_date=now.date(), due_date=now.date(),
                              bill_to_name="Synthetic Buyer", seller=BillingProfile(legal_name="Synthetic Seller", tax_number=SA_NUMBER),
                              sent_at=now, sent_by="user_test", content_hash="0" * 64, created_by="user_test")
            store.add("invoices", invoice)
            ids.append(invoice.id)
    return ids


def test_tax_tables_have_forced_rls_purge_policies_and_invoker_triggers(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)
        triggers = set(connection.execute(text("select tgname from pg_trigger where not tgisinternal")).scalars())
        assert {"business_zatca_chain_forward", "business_zatca_documents_frozen"} <= triggers
        definers = connection.execute(text("select count(*) from pg_proc where prosecdef and proname like 'business_zatca%'")).scalar_one()
        assert definers == 0


def test_the_counter_never_repeats_under_concurrent_generation_and_the_database_keeps_the_chain(
        client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:  # noqa: F811
    _setup(client)
    _tax(client, zatca_enabled=True)
    ids = _invoices(6)
    results: list[Z.ZatcaDocument] = []
    errors: list[BaseException] = []

    def run(invoice_id: str) -> None:
        try:
            results.append(Z.generate(CONTEXT, "invoice", invoice_id, actor="user_test")[0])
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(invoice_id,)) for invoice_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    ordered = sorted(results, key=lambda row: row.counter)
    assert [row.counter for row in ordered] == list(range(1, 7))
    assert ordered[0].previous_hash == Z.INITIAL_PREVIOUS_HASH
    assert [row.previous_hash for row in ordered[1:]] == [row.invoice_hash for row in ordered[:-1]]
    assert _rows(app_session, "select last_counter, last_hash from business_zatca_chains") == [(6, ordered[-1].invoice_hash)]
    # The counter only moves forward by one; generated data never changes.
    with pytest.raises(DBAPIError, match="only moves forward"):
        _rows(app_session, "update business_zatca_chains set last_counter = last_counter + 5", commit=True)
    with pytest.raises(DBAPIError, match="never changes"):
        _rows(app_session, "update business_zatca_documents set xml = '<x/>'", commit=True)
    with pytest.raises(DBAPIError, match="never changes"):
        _rows(app_session, "delete from business_zatca_documents", commit=True)
    # Another business of the same tenant reads none of it.
    for table in TABLES:
        assert _rows(app_session, f"select count(*) from {table}", workspace=WORKSPACE_A) == [(0,)]  # nosec B608: fixed table name
    assert _rows(app_session, "select count(*) from business_zatca_documents") == [(6,)]


def test_journal_lines_keep_their_tax_code_and_the_return_reads_them(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:  # noqa: F811
    _setup(client)
    _tax(client)
    vendor = client.post(f"{API}/vendors", headers=ME, json={"name": "Synthetic Software Abroad", "default_category": "software"}).json()
    made = client.post(f"{API}/bills", headers=ME, json={"vendor_id": vendor["id"], "description": "Synthetic licence", "currency": "SAR",
                                                         "bill_date": utc_now().date().isoformat(), "subtotal": "1000", "tax": "0",
                                                         "tax_code": "reverse_charge"})
    assert made.status_code == 201, made.text
    assert made.json()["reverse_charge_tax"] == "150.00"
    assert client.post(f"{API}/ledger/post", headers=ME).status_code == 200
    coded = _rows(app_session, "select coalesce(tax_code, '-'), sum(debit), sum(credit) from business_book_journal_lines where source_kind = 'bill' "
                               "group by tax_code order by 1")
    # The supplier is owed 1000 (no code); the cost, the input VAT and the self-assessed output VAT carry the code.
    assert coded == [("-", Decimal("0.0000"), Decimal("1000.0000")), ("reverse_charge", Decimal("1150.0000"), Decimal("150.0000"))]
    data = client.get(f"{TAX}/return", headers=ME).json()
    found = {row["box"]: row for row in data["boxes"]}
    assert found["9"]["vat"] == "150.00" and found["13"]["vat"] == "0.00" and data["journal"]["ties"]
    # A bill's tax code is a fact a closed period keeps: the trigger compares it too.
    bad = _rows(app_session, "select count(*) from business_book_bills where tax_code = 'reverse_charge' and reverse_charge_tax = 150")
    assert bad == [(1,)]
