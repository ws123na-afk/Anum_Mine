"""Statement import correctness (docs/finance-statements.md, docs/finance.md#statement-matching).

One test per defect found in the November 2026 import audit (ids I1 to I11), each written to fail on the code
before the fix. Every row, merchant, message and amount here is SYNTHETIC: invented names ("EXAMPLE CAFE",
"SAMPLE BOOKS", "مقهى تجريبي"), round fake amounts, and no wording or layout copied from a real statement.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import merchant_match as MM
from anum_api.finance import register_api
from anum_api.finance import sms_service as svc
from anum_api.finance import statement_match as M
from anum_api.finance.ledger import EntrySource, EntryStatus, JournalEntry
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_fix", "workspace_fix"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_fix", "x-user-roles": "owner"}
API = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender
NOV = date(2026, 11, 3)

client = TestClient(main.app)


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


def record(record_id: str, amount: str, day: date, text: str, *, primary: str = "sms", at: datetime | None = None,
           reference: str | None = None) -> M.Recorded:
    return M.Recorded(
        record_id=record_id, entry_id=record_id, canonical_id=record_id, transaction_id=f"txn_{record_id}", account_id="acc_1", posted_on=day,
        amount=Decimal(amount), currency="SAR", description=text, category=None, sources=frozenset({primary}), primary=primary,
        reference=reference, occurred_at=at, plain=primary in ("manual", "statement"),
    )


def line(key: int, amount: str, day: date, text: str, **extra: Any) -> M.Line:
    return M.Line(key=key, posted_on=day, amount=Decimal(amount), description=text, dedupe_hash=f"hash_{key}", **extra)


# -- I1: a different merchant is a second purchase ----------------------------------------------------------


def test_same_amount_with_a_different_merchant_is_not_held_as_a_duplicate() -> None:
    sms = record("s1", "-50.00", NOV, "EXAMPLE CAFE")
    out = M.match_lines([line(1, "-50.00", date(2026, 11, 4), "SAMPLE BOOKS")], [sms])
    assert (out[0].status, out[0].code) == ("new", "no_match") and out[0].default_import
    # Same amount, no merchant to compare: the person still decides.
    assert M.match_lines([line(1, "-50.00", NOV, "CARD TXN 0001")], [sms])[0].status == "possible_duplicate"


def test_a_different_merchant_at_the_records_own_time_is_still_offered_for_review() -> None:
    at = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    sms = record("s1", "-50.00", NOV, "EXAMPLE CAFE", at=at)
    out = M.match_lines([line(1, "-50.00", NOV, "SAMPLE BOOKS", at=at + timedelta(minutes=5))], [sms])
    assert out[0].status == "possible_duplicate"
    far = M.match_lines([line(1, "-50.00", NOV, "SAMPLE BOOKS", at=at + timedelta(hours=5))], [sms])
    assert far[0].status == "new"


# -- I2: generic banking words never make two merchants one --------------------------------------------------


def test_generic_banking_words_are_not_a_shared_merchant() -> None:
    assert MM.merchants_agree("POS PURCHASE EXAMPLE CAFE", "POS PURCHASE SAMPLE BOOKS") is False
    assert MM.merchants_agree("ONLINE PAYMENT TRANSFER", "ONLINE PAYMENT") is None  # nothing but generic words
    assert MM.merchants_agree("شراء عبر نقاط البيع مقهى تجريبي", "شراء عبر نقاط البيع مكتبة نموذجية") is False
    assert MM.merchants_agree("حوالة داخلية صادرة", "حوالة داخلية واردة") is None
    assert MM.merchants_agree("سداد بطاقة ائتمانية", "سداد فاتورة") is None
    # A real shared name still agrees, with or without the Arabic article.
    assert MM.merchants_agree("POS EXAMPLE CAFE RIYADH", "EXAMPLE CAFE 03/11") is True
    assert MM.merchants_agree("شراء مخبز النخلة", "النخلة") is True


def test_rows_sharing_only_generic_words_are_not_merged_as_the_same_statement_row() -> None:
    imported = M.Recorded(record_id="r1", entry_id="e1", canonical_id="e1", transaction_id="t1", account_id="acc_1", posted_on=NOV,
                          amount=Decimal("-100.00"), currency="SAR", description="ONLINE PURCHASE EXAMPLE CAFE", category=None,
                          sources=frozenset({"statement"}), primary="statement", statement_hashes=frozenset({"other"}), reconciled=True, plain=True)
    out = M.match_lines([line(1, "-100.00", NOV, "ONLINE PURCHASE SAMPLE BOOKS")], [imported])
    assert out[0].status == "new"
    arabic = M.Recorded(**{**imported.__dict__, "description": "حوالة داخلية صادرة مؤسسة تجريبية ألف"})
    out = M.match_lines([line(1, "-100.00", NOV, "حوالة داخلية صادرة مؤسسة نموذجية باء")], [arabic])
    assert out[0].status == "new"


def test_one_merchant_helper_is_shared_and_one_same_payment_window() -> None:
    from anum_api.finance import wallet

    assert wallet.merchants_agree is MM.merchants_agree and M.merchants_agree is MM.merchants_agree
    assert M.LAG_DAYS == register_api.DUPLICATE_DAYS == MM.SAME_PAYMENT_DAYS
    assert not hasattr(svc, "find_duplicate")


def test_the_register_does_not_flag_two_merchants_sharing_a_generic_word() -> None:
    from anum_api.finance.models import Category, CategorySource, Transaction

    now = datetime(2026, 11, 3, tzinfo=UTC)

    def row(row_id: str, text: str, day: date) -> Transaction:
        return Transaction(id=row_id, tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id="o", account_id="a", posted_on=day,
                           amount=Decimal("-20.00"), currency="SAR", description=text, category=Category.OTHER,
                           category_source=CategorySource.RULE, dedupe_hash=row_id, created_at=now, updated_at=now)

    rows = [row("t1", "POS PURCHASE EXAMPLE CAFE", NOV), row("t2", "POS PURCHASE SAMPLE BOOKS", NOV),
            row("t3", "EXAMPLE CAFE", date(2026, 11, 6))]
    found = register_api._duplicates(rows, {})
    assert found == {"t3": "t1"}  # the same cafe three days later; the bookshop is its own purchase


# -- the fingerprint read is complete and ordered -----------------------------------------------------------


def test_entries_with_a_fingerprint_are_read_completely_in_a_fixed_order() -> None:
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fix", roles=["owner"])
    now = datetime(2026, 11, 3, tzinfo=UTC)
    for number in range(260):
        sms_memory_store.entries[f"je_{number:04d}"] = (JournalEntry(
            id=f"je_{number:04d}", tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id="owner_fix", source=EntrySource.SMS,
            source_ref=f"m{number}", status=EntryStatus.POSTED, posted_on=date(2026, 1, 1) + timedelta(days=number % 300),
            kind="card_purchase", description="EXAMPLE", fingerprint="fp_same", created_at=now, postings=[],
        ))  # stored directly: only the read is under test
    every = sms_memory_store.entries_with_fingerprint(context, "fp_same")
    assert len(every) == 260
    keys = [(e.posted_on, e.created_at, e.id) for e in every]
    assert keys == sorted(keys, reverse=True)
    window = sms_memory_store.entries_with_fingerprint(context, "fp_same", start=date(2026, 3, 1), end=date(2026, 3, 31))
    assert window and all(date(2026, 3, 1) <= e.posted_on <= date(2026, 3, 31) for e in window)
