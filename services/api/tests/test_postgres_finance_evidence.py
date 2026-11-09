"""Business evidence and exception notice claims in PostgreSQL (migration ``0157_finance_evidence``), as the non-owner app role.

The vault link check takes the four business evidence types and nothing else; the evidence routes read links through
``vault_documents``, so another person's private document never shows; the evidence pack reads only what the caller may
read; ``finance_notice_claims`` has forced RLS with the tenant, owner and purge policies and claims an item once a day.
Every row is SYNTHETIC.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.business.books_store import books_memory_store
from anum_api.finance.rules_store import rules_store_for
from anum_api.finance.store import open_finance_store
from anum_api.schemas import TenantContext, utc_now

pytestmark = pytest.mark.database
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
OWNER = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "owner_1", "x-user-roles": "owner"}
ALICE = {**OWNER, "x-user-id": "alice", "x-user-roles": "member"}
API = "/api/v1/business/books"


def _members(engine: Engine) -> None:
    with engine.begin() as connection:
        for user_id, role in (("owner_1", "owner"), ("alice", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def _doc(client: TestClient, headers: dict, name: str, access: str) -> str:  # noqa: F811
    response = client.post("/api/v1/documents", params={"name": name, "title": f"Synthetic {name}", "category": "receipt", "access_class": access},
                           headers=headers, content=PDF + name.encode())
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_the_claims_table_has_forced_rls_and_the_vault_check_takes_only_the_new_types(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        table = "finance_notice_claims"
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
        assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        check = connection.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_vault_document_links_type'")).scalar_one()
        for kind in ("business_bill", "business_invoice", "business_adjustment", "business_journal", "finance_transaction"):
            assert kind in check
        column = connection.execute(text("select is_nullable from information_schema.columns where table_name = 'business_book_settings' "
                                         "and column_name = 'evidence_threshold'")).scalar_one()
        assert column == "YES"


def test_evidence_is_read_through_the_vault_policies_and_packed(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    books_memory_store.clear()
    settings = client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "SAR", "country": "SA", "fiscal_year_start_month": 1,
                                                                  "basis": "accrual", "evidence_threshold": "100", "version": None})
    assert settings.status_code == 200, settings.text
    assert settings.json()["evidence_threshold"] == "100.00"
    vendor = client.post(f"{API}/vendors", headers=OWNER, json={"name": "Synthetic Landlord", "default_category": "rent"}).json()
    bill = client.post(f"{API}/bills", headers=OWNER, json={"vendor_id": vendor["id"], "description": "Synthetic rent", "bill_date": date.today().isoformat(),
                                                            "currency": "SAR", "subtotal": "1000", "tax": "0", "category": "rent"}).json()
    shared = _doc(client, OWNER, "receipt.pdf", "workspace")
    private = _doc(client, ALICE, "alice.pdf", "private")
    assert client.post(f"{API}/evidence", headers=OWNER, json={"source_type": "bill", "source_id": bill["id"], "document_id": shared}).status_code == 201
    # A link to Alice's private document (written straight into the table) never reaches the owner: the join applies the policy.
    with database_engine.begin() as connection:
        connection.execute(text("insert into vault_document_links (tenant_id, workspace_id, id, document_id, target_type, target_id, label, status, created_by) "
                                "values (:t, :w, 'vlnk_synthetic', :d, 'business_bill', :b, '', 'confirmed', 'alice')"),
                           {"t": TENANT_A, "w": WORKSPACE_A, "d": private, "b": bill["id"]})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("insert into vault_document_links (tenant_id, workspace_id, id, document_id, target_type, target_id, created_by) "
                                    "values (:t, :w, 'vlnk_bad', :d, 'business_secret', 'x', 'alice')"), {"t": TENANT_A, "w": WORKSPACE_A, "d": shared})
    seen = client.get(f"{API}/evidence", headers=OWNER, params={"source_type": "bill", "ids": bill["id"]}).json()["items"][bill["id"]]
    assert [d["document_id"] for d in seen["documents"]] == [shared] and seen["evidence_missing"] is False
    hers = client.get(f"{API}/evidence", headers=ALICE, params={"source_type": "bill", "ids": bill["id"]}).json()["items"][bill["id"]]
    assert sorted(d["document_id"] for d in hers["documents"]) == sorted([shared, private])
    client.post(f"{API}/ledger/post", headers=OWNER)
    pack = client.get(f"{API}/evidence-pack.zip", headers=OWNER)
    assert pack.status_code == 200, pack.text
    names = zipfile.ZipFile(io.BytesIO(pack.content)).namelist()
    assert any(n.startswith(f"documents/{shared}-") for n in names) and not any(private in n for n in names)


def test_a_notice_claim_is_the_owners_and_taken_once_a_day(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    me = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_1")
    other = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="alice")
    today, now = date.today(), utc_now()
    with open_finance_store(me) as finance:
        store = rules_store_for(finance)
        assert store.claim_item_notices(me, "uncategorized_money", ["uncategorized:SAR"], today, now) == ["uncategorized:SAR"]
        assert store.claim_item_notices(me, "uncategorized_money", ["uncategorized:SAR", "uncategorized:AED"], today, now) == ["uncategorized:AED"]
    with open_finance_store(other) as finance:
        store = rules_store_for(finance)
        # Alice sees none of the owner's claims, and claims the same item for herself.
        assert store.claim_item_notices(other, "uncategorized_money", ["uncategorized:SAR"], today, now) == ["uncategorized:SAR"]
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'alice', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from finance_notice_claims")).scalar_one() == 1
    with open_finance_store(me) as finance:
        store = rules_store_for(finance)
        store.release_item_notices(me, "uncategorized_money", ["uncategorized:AED"], today)
        assert store.claim_item_notices(me, "uncategorized_money", ["uncategorized:AED"], today, now) == ["uncategorized:AED"]
        assert store.delete_everything(me) >= 2
