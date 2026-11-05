"""PDF statements in PostgreSQL (migration ``0139_finance_pdf_templates``, docs/finance-statements.md).

The API runs as the non-owner ``anum_test_app`` role. A taught statement layout is visible only to its
owner in its tenant and workspace; a PDF import goes through the same journal and reconciliation as a
CSV import, and is undone the same way. Statements are SYNTHETIC, generated in the test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.pdf_templates import template_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings
from test_statement_pdf_engine import card_statement

pytestmark = pytest.mark.database
API = "/api/v1/finance"
TABLE = "finance_statement_templates"


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
    template_memory_store.clear()
    yield TestClient(app)


def _count(app_session: Callable[..., Iterator[Session]], user_id: str) -> int:
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(f"select count(*) from {TABLE}")).scalar_one())  # nosec B608 - fixed table name


def test_the_templates_table_has_forced_rls_an_owner_policy_and_a_purge_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": TABLE}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": TABLE}).scalars())
    assert forced and {f"tenant_isolation_{TABLE}", f"owner_only_{TABLE}", f"lifecycle_purge_{TABLE}"} <= policies


def test_a_pdf_import_and_a_taught_layout_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    account = client.post(f"{API}/accounts", headers=ME, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    view = client.post(f"{API}/imports/pdf/read", headers={**ME, "content-type": "application/pdf"},
                       params={"account_id": account["id"], "filename": "card.pdf"}, content=card_statement()).json()
    assert view["check"]["status"] == "matched" and len(view["rows"]) == 5
    roles = list(view["roles"])
    roles[roles.index("fee")] = "skip"
    body = {"account_id": account["id"], "filename": "card.pdf", "file_sha256": view["file_sha256"], "source": "text",
            "rows": [{"posted_on": r["posted_on"], "description": r["description"], "amount": r["amount"]} for r in view["rows"]],
            "opening_balance": view["check"]["opening"], "closing_balance": view["check"]["closing"],
            "layout": {"fingerprint": view["fingerprint"], "labels": [c["label"] for c in view["columns"]], "roles": roles, "remember": True}}
    imported = client.post(f"{API}/imports/pdf", headers=ME, json=body)
    assert imported.status_code == 201, imported.text
    assert imported.json()["imported_count"] == 5 and imported.json()["preset"] == "pdf"
    assert _count(app_session, "user_test") == 1 and _count(app_session, "user_owner") == 0
    assert client.get(f"{API}/imports/pdf/templates", headers=SOMEONE).json() == []
    template = client.get(f"{API}/imports/pdf/templates", headers=ME).json()[0]
    assert template["roles"] == roles
    # The same layout is read with the taught mapping next time; the import is undone like a CSV one.
    again = client.post(f"{API}/imports/pdf/read", headers={**ME, "content-type": "application/pdf"},
                        params={"account_id": account["id"]}, content=card_statement()).json()
    assert again["template_id"] == template["id"] and all(r["duplicate"] for r in again["rows"])
    assert client.delete(f"{API}/imports/{imported.json()['id']}", headers=ME).status_code == 204
    assert client.get(f"{API}/transactions", headers=ME).json()["items"] == []
    assert client.delete(f"{API}/imports/pdf/templates/{template['id']}", headers=SOMEONE).status_code == 404
    # Delete my data removes the taught layouts too.
    ctx = tenant_context()
    from anum_api.finance.store import open_finance_store

    with open_finance_store(ctx) as store:
        store.delete_everything(ctx)
    assert _count(app_session, "user_test") == 0
