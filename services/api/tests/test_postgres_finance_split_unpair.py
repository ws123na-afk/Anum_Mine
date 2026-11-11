"""One statement row for several recorded payments, and unpairing an own transfer, in PostgreSQL
(docs/finance.md#one-row-for-several-recorded-payments, docs/finance.md#own-transfers).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. A picked group is attached whole and undone
whole (the journal entries' reconciled marks are cleared in the database); a transfer pair is read and unpaired through
the links API; another person sees none of it. Every row is SYNTHETIC ("EXAMPLE").
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
SOMEONE = {**ME, "x-user-id": "user_owner"}
ROW = "Date,Description,Amount\n2026-11-04,EXAMPLE MARKET RIYADH,-150.00\n"


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield TestClient(app)


def _ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


def _account(client: TestClient, **body: object) -> dict:
    return _ok(client.post(f"{API}/accounts", headers=ME, json={"currency": "SAR", "kind": "bank", **body}), 201)


def _entry(client: TestClient, account_id: str, amount: str, text_: str, day: str) -> dict:
    return _ok(client.post(f"{API}/transactions", headers=ME, json={"account_id": account_id, "amount": amount, "description": text_, "posted_on": day}), 201)


def test_a_group_is_attached_and_undone_whole(client: TestClient, database_engine: Engine) -> None:
    card = _account(client, name="Card", kind="card", last4="1234")
    _entry(client, card["id"], "-100.00", "EXAMPLE MARKET", "2026-11-03")
    _entry(client, card["id"], "-50.00", "EXAMPLE MARKET", "2026-11-03")
    view = _ok(client.post(f"{API}/imports/preview", headers=ME, json={"account_id": card["id"], "content": ROW, "filename": "nov.csv"}))
    group = view["rows"][0]["match"]["splits"][0]
    done = _ok(client.post(f"{API}/imports", headers=ME, json={"account_id": card["id"], "content": ROW, "filename": "nov.csv",
                                                               "decisions": [{"line": 2, "split": [r["record_id"] for r in group]}]}), 201)
    assert (done["imported_count"], done["split_matched"]) == (0, 1)
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from finance_journal_entries where reconciled_at is not null")).scalar_one() == 2
    group_id = _ok(client.get(f"{API}/imports/{done['id']}", headers=ME))["rows"][0]["split_group"]
    assert client.post(f"{API}/imports/{done['id']}/splits/{group_id}/undo", headers=SOMEONE).status_code == 404
    undone = _ok(client.post(f"{API}/imports/{done['id']}/splits/{group_id}/undo", headers=ME))
    assert (undone["split_matched"], undone["held_for_review"]) == (0, 1)
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from finance_journal_entries where reconciled_at is not null")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_sms_messages where source = 'statement' and status = 'review'")).scalar_one() == 1


def test_a_transfer_pair_is_read_and_unpaired_through_the_links_api(client: TestClient) -> None:
    bank = _account(client, name="Salary account", last4="5678")
    card = _account(client, name="Card", kind="card", last4="5377")
    out = _entry(client, bank["id"], "-1000.00", "EXAMPLE CARD PAYMENT 5377", "2026-11-03")
    _entry(client, card["id"], "1000.00", "EXAMPLE PAYMENT", "2026-11-05")
    window = {"start": "2026-11-01", "end": "2026-11-30"}
    pair = _ok(client.get(f"{API}/transfers/candidates", headers=ME, params=window))["pairs"][0]
    _ok(client.post(f"{API}/transfers", headers=ME, json={**window, "pairs": [{"out_record_id": pair["out"]["record_id"],
                                                                                "into_record_id": pair["into"]["record_id"]}]}), 201)
    side = _ok(client.get(f"{API}/links/transfer", headers=ME, params={"transaction_id": out["id"]}))
    assert side["paired"] and side["role"] == "out" and side["other"]["account_name"] == "Card"
    assert client.get(f"{API}/links/transfer", headers=SOMEONE, params={"transaction_id": out["id"]}).status_code == 404
    _ok(client.post(f"{API}/links/{side['link_id']}/void", headers=ME, json={"version": side["link_version"]}))
    assert _ok(client.get(f"{API}/links/transfer", headers=ME, params={"transaction_id": out["id"]}))["paired"] is False
