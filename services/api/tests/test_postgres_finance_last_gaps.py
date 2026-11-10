"""Dismissed receipt proposals and the owner's VAT filing deadline in PostgreSQL (migration ``0159_finance_last_gaps``).

As the non-owner app role under forced RLS: the dismissals table has the tenant and purge policies and one row per bill
and document, a dismissed receipt is not proposed again until restored, another business reads none of it, and the
new tax settings columns refuse values outside their range. Every row is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.business.books_store import books_memory_store

pytestmark = pytest.mark.database
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
OWNER = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "owner_1", "x-user-roles": "owner"}
ALICE = {**OWNER, "x-user-id": "alice", "x-user-roles": "member"}
API = "/api/v1/business/books"
TABLE = "business_book_receipt_dismissals"


def _members(engine: Engine) -> None:
    with engine.begin() as connection:
        for user_id, role in (("owner_1", "owner"), ("alice", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def test_the_dismissals_table_has_forced_rls_and_the_tax_columns_are_nullable(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": TABLE}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": TABLE}).scalars())
        assert forced and {f"tenant_isolation_{TABLE}", f"lifecycle_purge_{TABLE}"} <= policies
        for column in ("country", "filing_due_days"):
            assert connection.execute(text("select is_nullable from information_schema.columns where table_name = 'business_tax_settings' "
                                           "and column_name = :c"), {"c": column}).scalar_one() == "YES"


def test_a_dismissed_receipt_stays_out_until_restored_and_other_businesses_read_nothing(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    books_memory_store.clear()
    assert client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "SAR", "version": None}).status_code == 200
    vendor = client.post(f"{API}/vendors", headers=OWNER, json={"name": "Synthetic Landlord", "default_category": "rent"}).json()
    bill = client.post(f"{API}/bills", headers=OWNER, json={"vendor_id": vendor["id"], "description": "Synthetic rent", "bill_date": date.today().isoformat(),
                                                            "currency": "SAR", "subtotal": "1150", "tax": "0", "category": "rent"}).json()
    uploaded = client.post("/api/v1/documents", params={"name": "rent.pdf", "title": "Synthetic rent receipt", "category": "receipt",
                                                        "access_class": "workspace"}, headers=OWNER, content=PDF)
    assert uploaded.status_code == 201, uploaded.text
    doc = uploaded.json()["id"]
    with database_engine.begin() as connection:  # the values a person confirmed in the vault
        connection.execute(text("update vault_documents set receipt = cast(:r as jsonb) where id = :d"), {
            "d": doc, "r": '{"merchant": "Synthetic Landlord LLC", "date": "%s", "total": "1150", "currency": "SAR", "items": []}' % date.today().isoformat()})
    matches = f"{API}/evidence/receipt-matches"
    assert [m["document_id"] for m in client.get(matches, headers=OWNER).json()["items"]] == [doc]
    pair = {"bill_id": bill["id"], "document_id": doc}
    assert client.post(f"{matches}/dismiss", headers=ALICE, json=pair).status_code == 403
    dismissed = client.post(f"{matches}/dismiss", headers=OWNER, json=pair)
    assert dismissed.status_code == 201, dismissed.text
    assert client.post(f"{matches}/dismiss", headers=OWNER, json=pair).status_code == 200  # the first dismissal again
    assert client.get(matches, headers=OWNER).json()["items"] == []
    listed = client.get(f"{matches}/dismissed", headers=OWNER).json()
    assert [(row["bill_id"], row["document_title"]) for row in listed["items"]] == [(bill["id"], "Synthetic rent receipt")]

    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', 'ws_other_synthetic', true)"),
                           {"t": TENANT_A})
        assert connection.execute(text(f"select count(*) from {TABLE}")).scalar_one() == 0
    with database_engine.begin() as connection, pytest.raises(DBAPIError):  # one row per bill and document
        connection.execute(text(f"insert into {TABLE} (id, tenant_id, workspace_id, bill_id, document_id, dismissed_by) "
                                "values ('brdis_twin', :t, :w, :b, :d, 'owner_1')"), {"t": TENANT_A, "w": WORKSPACE_A, "b": bill["id"], "d": doc})

    assert client.delete(f"{matches}/dismissed/{dismissed.json()['id']}", headers=OWNER).status_code == 204
    assert [m["document_id"] for m in client.get(matches, headers=OWNER).json()["items"]] == [doc]


def test_the_filing_settings_are_stored_and_checked_by_the_database(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    books_memory_store.clear()
    assert client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "OMR", "version": None}).status_code == 200
    saved = client.put(f"{API}/tax/settings", headers=OWNER, json={"jurisdiction": "OTHER", "registered": True, "registration_number": "OM-SYNTHETIC-1",
                                                                  "country": "OM", "filing_due_days": 25, "version": None})
    assert saved.status_code == 200, saved.text
    overview = client.get(f"{API}/tax", headers=OWNER).json()
    assert (overview["settings"]["country"], overview["settings"]["filing_due_days"], overview["filing_deadline"]["source"]) == ("OM", 25, "owner")
    for sql in ("update business_tax_settings set filing_due_days = 0", "update business_tax_settings set country = 'om'"):
        with database_engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(text(sql))
