"""One statement row matched to several recorded payments (docs/finance.md#one-row-for-several-recorded-payments).

In-memory stores, SYNTHETIC rows and messages. The engine only offers groups that add up exactly; nothing is applied
unless the person picks one; the import attaches all of the group or none of it; the match is undone as a whole (the
row then waits in Review) and undoing the import detaches it too.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import sms_service as svc
from anum_api.finance import statement_match as M
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

from test_finance_statement_match import API, OTHER_PERSON, OWNER, VIEWER, do_import, preview, send_sms, sms_setup, transactions

client = TestClient(main.app)
FIRST = "SYNTHETIC. Purchase of SAR 100.00 at EXAMPLE MARKET with card **1234 on 03/11/2026 14:22. Ref 11AA01"
SECOND = "SYNTHETIC. Purchase of SAR 50.00 at EXAMPLE MARKET with card **1234 on 03/11/2026 14:40. Ref 22BB02"
ROW = "Date,Description,Amount\n2026-11-04,POS EXAMPLE MARKET RIYADH,-150.00\n2026-11-04,EXAMPLE BAKERY,-9.00\n"


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


def recorded(record_id: str, amount: str, day: int, text: str = "EXAMPLE MARKET", **extra: object) -> M.Recorded:
    return M.Recorded(record_id=record_id, entry_id=f"e_{record_id}", canonical_id=f"e_{record_id}", transaction_id=None, account_id="a",
                      posted_on=date(2026, 11, day), amount=Decimal(amount), currency="SAR", description=text, category=None,
                      sources=frozenset({"sms"}), primary="sms", **extra)  # type: ignore[arg-type]


def line(amount: str, day: int = 4, text: str = "POS EXAMPLE MARKET") -> M.Line:
    return M.Line(key=1, posted_on=date(2026, 11, day), amount=Decimal(amount), description=text, dedupe_hash="h")


def test_only_exact_groups_are_offered_and_never_against_a_disagreeing_merchant() -> None:
    pool = [recorded("r1", "-100.00", 3), recorded("r2", "-50.00", 3), recorded("r3", "-49.99", 3), recorded("r4", "-50.00", 3, "OTHER SHOP NAME")]
    groups = M.split_options(line("-150.00"), pool)
    assert [sorted(r.record_id for r in g) for g in groups] == [["r1", "r2"]]
    assert M.split_options(line("-150.01"), pool) == []  # no tolerance
    assert M.split_options(line("150.00"), pool) == []  # the other direction
    late = [recorded("r1", "-100.00", 1), recorded("r2", "-50.00", 3)]  # 3 days is the window
    assert M.split_options(line("-150.00", day=5), late) == []
    confirmed = [recorded("r1", "-100.00", 3, reconciled=True), recorded("r2", "-50.00", 3)]
    assert M.split_options(line("-150.00"), confirmed) == []
    assert M.split_options(line("-150.00"), [recorded("r1", "-150.00", 3), recorded("r2", "-0.00", 3)]) == []


def test_groups_of_up_to_four_ranked_by_agreement() -> None:
    pool = [recorded(f"r{i}", "-25.00", 3) for i in range(6)]
    groups = M.split_options(line("-100.00"), pool)
    assert len(groups) == M.MAX_SPLIT_OPTIONS and all(len(g) == 4 for g in groups)
    assert M.split_options(line("-125.00"), pool) == []  # five records would be needed


def test_the_person_picks_a_group_and_all_of_it_is_attached() -> None:
    s = sms_setup()
    assert send_sms(s["token"], FIRST)["status"] == "posted" and send_sms(s["token"], SECOND)["status"] == "posted"
    view = preview(s["card"]["id"], ROW)
    row = view["rows"][0]["match"]
    assert row["status"] == "new" and len(row["splits"]) == 1 and view["counts"]["splits"] == 1
    group = row["splits"][0]
    assert sorted(Decimal(str(r["amount"])) for r in group) == [Decimal("-100.00"), Decimal("-50.00")] and {r["source"] for r in group} == {"sms"}
    before = len(transactions(s["card"]["id"]))
    done = do_import(s["card"]["id"], ROW, decisions=[{"line": 2, "split": [r["record_id"] for r in group]}])
    assert (done["imported_count"], done["reconciled"], done["split_matched"]) == (1, 1, 1)
    assert len(transactions(s["card"]["id"])) == before + 1  # only the bakery row was added
    detail = client.get(f"{API}/imports/{done['id']}", headers=OWNER, params={"disposition": "attached_to_existing"}).json()
    matched = detail["rows"][0]
    assert matched["reason"] == "split_match" and len(matched["split_records"]) == 2 and matched["split_group"]
    # The same statement again: the row is already recorded, as confirmed before.
    again = preview(s["card"]["id"], ROW)
    assert again["rows"][0]["match"]["status"] == "exact_duplicate" and again["rows"][0]["match"]["code"] == "reconciled_before"


def test_without_a_pick_nothing_is_attached() -> None:
    s = sms_setup()
    send_sms(s["token"], FIRST)
    send_sms(s["token"], SECOND)
    done = do_import(s["card"]["id"], ROW)
    assert (done["imported_count"], done["split_matched"]) == (2, 0)


def test_a_group_that_changed_since_the_preview_is_held_whole() -> None:
    s = sms_setup()
    send_sms(s["token"], FIRST)
    send_sms(s["token"], SECOND)
    group = preview(s["card"]["id"], ROW)["rows"][0]["match"]["splits"][0]
    ids = [r["record_id"] for r in group]
    # A made-up member, or a group of one real and one other record: none of it is attached.
    for picked in ([ids[0], "fin_jrn_missing:acct"], [ids[0]] * 2):
        sms_memory_store.messages = {k: v for k, v in sms_memory_store.messages.items() if v.source != "statement"}
        done = do_import(s["card"]["id"], ROW.replace("EXAMPLE BAKERY", f"EXAMPLE BAKERY {len(picked)}{picked[1][-4:]}"),
                         decisions=[{"line": 2, "split": picked}])
        assert done["split_matched"] == 0 and done["held_for_review"] >= 1
    assert client.post(f"{API}/imports", headers=OWNER, json={"account_id": s["card"]["id"], "content": ROW, "filename": "nov.csv",
                                                              "decisions": [{"line": 2, "split": [ids[0]]}]}).status_code == 422


def test_undoing_the_match_detaches_every_record_and_the_row_waits_in_review() -> None:
    s = sms_setup()
    send_sms(s["token"], FIRST)
    send_sms(s["token"], SECOND)
    group = preview(s["card"]["id"], ROW)["rows"][0]["match"]["splits"][0]
    done = do_import(s["card"]["id"], ROW, decisions=[{"line": 2, "split": [r["record_id"] for r in group]}])
    group_id = client.get(f"{API}/imports/{done['id']}", headers=OWNER).json()["rows"][0]["split_group"]
    url = f"{API}/imports/{done['id']}/splits/{group_id}/undo"
    assert client.post(url, headers=VIEWER).status_code == 403
    assert client.post(url, headers=OTHER_PERSON).status_code == 404
    undone = client.post(url, headers=OWNER)
    assert undone.status_code == 200, undone.text
    assert (undone.json()["split_matched"], undone.json()["reconciled"], undone.json()["held_for_review"]) == (0, 0, 1)
    assert client.post(url, headers=OWNER).status_code == 409
    # The records are free again: a new preview of the statement offers the group (the row itself waits in Review).
    review = [m for m in sms_memory_store.messages.values() if m.source == "statement" and m.status == "review"]
    assert len(review) == 1 and Decimal(str(review[0].parsed["amount"])) == Decimal("150.00")
    assert all(e.reconciled_at is None for e in sms_memory_store.entries.values() if e.source.value in ("sms", "device"))


def test_undoing_the_import_detaches_the_group_too() -> None:
    s = sms_setup()
    send_sms(s["token"], FIRST)
    send_sms(s["token"], SECOND)
    group = preview(s["card"]["id"], ROW)["rows"][0]["match"]["splits"][0]
    done = do_import(s["card"]["id"], ROW, decisions=[{"line": 2, "split": [r["record_id"] for r in group]}])
    assert client.delete(f"{API}/imports/{done['id']}", headers=OWNER).status_code == 204
    fresh = preview(s["card"]["id"], ROW)["rows"][0]["match"]
    assert fresh["status"] == "new" and len(fresh["splits"]) == 1


def test_a_pick_on_a_row_that_is_already_recorded_is_ignored() -> None:
    """A stale preview's pick on a row that has since been recorded neither attaches nor holds it twice."""
    s = sms_setup()
    send_sms(s["token"], FIRST)
    send_sms(s["token"], SECOND)
    group = preview(s["card"]["id"], ROW)["rows"][0]["match"]["splits"][0]
    ids = [r["record_id"] for r in group]
    do_import(s["card"]["id"], ROW, decisions=[{"line": 2, "split": ids}])
    again = do_import(s["card"]["id"], ROW, decisions=[{"line": 2, "split": ids}])
    assert (again["split_matched"], again["held_for_review"], again["duplicate_count"]) == (0, 0, 2)
