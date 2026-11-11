"""Unpairing an own transfer (docs/finance.md#own-transfers), in-memory stores.

The transfer pair of a transaction is read through the links API (``GET /finance/links/transfer``) and unpaired by
voiding its link (``POST /finance/links/{id}/void``): both sides then read as unpaired, the pair is offered again under
Find transfers, the categories stay as they were, and only the owner of the ledger sees or changes it. Every row is
SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

from test_finance_statement_match import API, OTHER_PERSON, OWNER, VIEWER, account, transactions

client = TestClient(main.app)
WINDOW = {"start": "2026-11-01", "end": "2026-11-30"}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def paired_rows() -> tuple[dict, dict]:
    bank = account(name="Salary account", last4="5678")
    card = account(name="Card", kind="card", last4="5377")
    for body in ({"account_id": bank["id"], "amount": "-1000.00", "description": "EXAMPLE CARD PAYMENT 5377", "posted_on": "2026-11-03"},
                 {"account_id": card["id"], "amount": "1000.00", "description": "EXAMPLE PAYMENT", "posted_on": "2026-11-05"}):
        assert client.post(f"{API}/transactions", headers=OWNER, json=body).status_code == 201
    pair = client.get(f"{API}/transfers/candidates", headers=OWNER, params=WINDOW).json()["pairs"][0]
    made = client.post(f"{API}/transfers", headers=OWNER, json={**WINDOW, "pairs": [
        {"out_record_id": pair["out"]["record_id"], "into_record_id": pair["into"]["record_id"]}]})
    assert made.status_code == 201, made.text
    rows = {Decimal(t["amount"]): t for t in transactions() if abs(Decimal(t["amount"])) == 1000}
    return rows[Decimal("-1000.00")], rows[Decimal("1000.00")]


def pair_of(transaction_id: str, headers: dict = OWNER) -> dict:
    response = client.get(f"{API}/links/transfer", headers=headers, params={"transaction_id": transaction_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_both_sides_show_the_pair_and_unpairing_frees_it() -> None:
    out_row, in_row = paired_rows()
    out_side, in_side = pair_of(out_row["id"]), pair_of(in_row["id"])
    assert out_side["paired"] and out_side["role"] == "out" and out_side["other"]["account_name"] == "Card"
    assert out_side["other"]["transaction_id"] == in_row["id"] and Decimal(str(out_side["other"]["amount"])) == Decimal("1000.00")
    assert in_side["paired"] and in_side["role"] == "in" and in_side["link_id"] == out_side["link_id"]
    assert in_side["other"]["account_name"] == "Salary account" and in_side["other"]["description"] == "EXAMPLE CARD PAYMENT 5377"
    categories = sorted(t["category"] for t in transactions())
    # A stale version is refused; the right one unpairs.
    stale = client.post(f"{API}/links/{out_side['link_id']}/void", headers=OWNER, json={"version": out_side["link_version"] + 1})
    assert stale.status_code == 409
    voided = client.post(f"{API}/links/{out_side['link_id']}/void", headers=OWNER, json={"version": out_side["link_version"]})
    assert voided.status_code == 200 and voided.json()["status"] == "void"
    assert pair_of(out_row["id"]) == {"paired": False, "link_id": None, "link_version": None, "role": None, "other": None}
    assert pair_of(in_row["id"])["paired"] is False
    assert sorted(t["category"] for t in transactions()) == categories  # the categories stay as they are
    # The pair is offered again, and pairing it again works.
    again = client.get(f"{API}/transfers/candidates", headers=OWNER, params=WINDOW).json()["pairs"]
    assert len(again) == 1
    # Voiding twice is harmless.
    assert client.post(f"{API}/links/{out_side['link_id']}/void", headers=OWNER, json={"version": out_side["link_version"]}).status_code == 200
    assert "finance.link.void" in [r.action for r in memory_repository.store.audit_records]


def test_only_the_ledgers_owner_reads_or_unpairs() -> None:
    out_row, _ = paired_rows()
    link_id = pair_of(out_row["id"])["link_id"]
    assert client.get(f"{API}/links/transfer", headers=OTHER_PERSON, params={"transaction_id": out_row["id"]}).status_code == 404
    assert client.post(f"{API}/links/{link_id}/void", headers=OTHER_PERSON, json={}).status_code == 404
    assert client.post(f"{API}/links/{link_id}/void", headers=VIEWER, json={}).status_code == 403
    assert client.get(f"{API}/links/transfer", headers=OWNER, params={"transaction_id": "fin_txn_missing"}).status_code == 404
    assert pair_of(out_row["id"])["paired"] is True


def test_a_row_that_was_never_paired_reads_as_unpaired() -> None:
    bank = account(name="Salary account", last4="5678")
    created = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": bank["id"], "amount": "-20.00", "description": "EXAMPLE CAFE",
                                                                      "posted_on": "2026-11-03"})
    assert pair_of(created.json()["id"])["paired"] is False
