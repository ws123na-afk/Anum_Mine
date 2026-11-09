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
from anum_api.finance import statement_pdf as S
from anum_api.finance.ledger import EntrySource, EntryStatus, JournalEntry
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.pdf_writer import PdfDocument
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


# -- the PDF reader: I4, I7, I8, I9, I10 ---------------------------------------------------------------------


Cell = tuple  # (x, y, text) or (x, y, text, right_edge) for right-aligned figures
HEADER: list[Cell] = [(40, 760, "Date"), (110, 760, "Description"), (330, 760, "Debit"), (410, 760, "Credit"), (490, 760, "Balance")]


def draw(pages: list[list[Cell]]) -> bytes:
    document = PdfDocument("SYNTHETIC")
    for number, cells in enumerate(pages):
        if number:
            document._new_page()
        for cell in cells:
            if len(cell) > 3:
                document.page.text(cell[3] - 200, cell[1], cell[2], 8, align="right", width=200)
            else:
                document.page.text(cell[0], cell[1], cell[2], 8)
    return document.render()


def read_pdf(data: bytes, currency: str = "SAR") -> S.Statement:
    extracted = S.extract(data)
    return S.parse(extracted.pages, currency=currency, page_widths=extracted.widths)


def money_row(y: float, when: str, text: str, debit: str, credit: str, balance: str) -> list[Cell]:
    return [(40, y, when), (110, y, text), (0, y, debit, 370), (0, y, credit, 450), (0, y, balance, 530)]


def test_a_current_account_whose_rows_mention_a_credit_card_keeps_its_balance_check() -> None:
    cells: list[Cell] = [(150, 800, "SYNTHETIC Current Account Statement"), *HEADER, (110, 745, "Opening balance"), (0, 745, "1,000.00", 530)]
    cells += money_row(730, "02/11/2026", "سداد بطاقة ائتمانية", "200.00", "", "800.00")
    cells += money_row(716, "03/11/2026", "CREDIT CARD PAYMENT EXAMPLE", "100.00", "", "700.00")
    cells += money_row(702, "04/11/2026", "EXAMPLE CAFE", "50.00", "", "650.00")
    cells += [(110, 688, "Closing balance"), (0, 688, "650.00", 530)]
    statement = read_pdf(draw([cells]))
    assert statement.kind == "account"
    assert statement.check.status == "matched" and statement.closing_balance == Decimal("650.00")
    wrong = read_pdf(draw([[*cells[:-1], (0, 688, "640.00", 530)]]))
    assert wrong.kind == "account" and wrong.check.status == "mismatch"  # the check runs, so a wrong closing shows


def test_a_card_statement_is_still_told_from_its_title() -> None:
    cells: list[Cell] = [(150, 800, "SYNTHETIC Credit Card Statement"), (40, 760, "Date"), (110, 760, "Description"), (330, 760, "Amount"),
                         (40, 745, "02/11/2026"), (110, 745, "EXAMPLE CAFE"), (0, 745, "50.00", 370)]
    assert read_pdf(draw([cells])).kind == "card"


def test_a_row_with_a_gregorian_and_a_hijri_date_and_its_amounts_on_the_next_line_is_read() -> None:
    header: list[Cell] = [(40, 760, "Date"), (100, 760, "Hijri Date"), (170, 760, "Description"), (330, 760, "Debit"), (410, 760, "Credit"),
                          (490, 760, "Balance"), (170, 748, "Opening balance"), (0, 748, "500.00", 530)]
    cells = [*header,
             (40, 730, "05/11/2026"), (100, 730, "14/05/1448"), (170, 730, "EXAMPLE CAFE"),
             (0, 722, "25.00", 370), (0, 722, "475.00", 530),
             (40, 700, "06/11/2026"), (100, 700, "15/05/1448"), (170, 700, "Transfer from EXAMPLE PERSON"),
             (0, 692, "100.00", 450), (0, 692, "575.00", 530),
             (170, 670, "Closing balance"), (0, 670, "575.00", 530)]
    statement = read_pdf(draw([cells]))
    assert [(r.posted_on, r.amount) for r in statement.rows] == [(date(2026, 11, 5), Decimal("-25.00")), (date(2026, 11, 6), Decimal("100.00"))]
    assert statement.period_start is None  # "Transfer from" never sets the period
    assert statement.check.status == "matched"


def test_a_period_printed_over_two_lines_is_read() -> None:
    cells: list[Cell] = [(40, 800, "From 01/11/2026"), (40, 788, "To 30/11/2026"), *HEADER, (110, 745, "Opening balance"), (0, 745, "100.00", 530)]
    cells += money_row(730, "02/11/2026", "EXAMPLE CAFE", "10.00", "", "90.00")
    statement = read_pdf(draw([cells]))
    assert (statement.period_start, statement.period_end) == (date(2026, 11, 1), date(2026, 11, 30))
    # The test PDF writer sets Arabic glyphs about four points lower than digits: drawn four points up, they share a line.
    arabic: list[Cell] = [(40, 800, "01/11/2026"), (100, 804, "من"), (40, 786, "30/11/2026"), (100, 790, "إلى"), *cells[2:]]
    statement = read_pdf(draw([arabic]))
    assert (statement.period_start, statement.period_end) == (date(2026, 11, 1), date(2026, 11, 30))


def test_a_transaction_page_with_a_terms_and_conditions_footer_is_read() -> None:
    cells: list[Cell] = [*HEADER, (110, 745, "Opening balance"), (0, 745, "100.00", 530)]
    cells += money_row(730, "02/11/2026", "EXAMPLE CAFE", "10.00", "", "90.00")
    cells += money_row(716, "03/11/2026", "SAMPLE BOOKS", "20.00", "", "70.00")
    cells += [(40, 120, "Terms and conditions apply. An example of the fees is on our website.")]
    statement = read_pdf(draw([cells]))
    assert [r.description for r in statement.rows] == ["EXAMPLE CAFE", "SAMPLE BOOKS"]
    assert statement.skipped_pages == [] and statement.check.status == "matched"
    # A page of terms with no row is still skipped whole.
    terms: list[Cell] = [(40, 800, "Terms and conditions"), (40, 780, "An example of how interest is worked out.")]
    assert read_pdf(draw([cells, terms])).skipped_pages == [(2, "not_transactions")]


def test_rows_outside_the_period_are_reported_never_dropped_silently() -> None:
    cells: list[Cell] = [(40, 800, "Statement period 01/11/2026 to 30/11/2026"), *HEADER]
    cells += money_row(730, "02/11/2026", "EXAMPLE CAFE", "10.00", "", "90.00")
    cells += money_row(716, "15/01/2025", "SAMPLE OLD ROW", "20.00", "", "70.00")
    statement = read_pdf(draw([cells]))
    assert [r.description for r in statement.rows] == ["EXAMPLE CAFE"]
    assert [(d.reason, d.posted_on, d.text) for d in statement.dropped] == [("outside_period", date(2025, 1, 15), "SAMPLE OLD ROW")]
    assert "rows_left_out" in statement.warnings


def test_without_a_printed_period_rows_months_before_the_statement_date_are_kept() -> None:
    cells: list[Cell] = [(40, 800, "Statement Date"), (200, 800, "30/11/2026"), *HEADER]
    cells += money_row(730, "05/06/2026", "EXAMPLE CAFE", "10.00", "", "90.00")  # about 180 days before
    cells += money_row(716, "02/11/2026", "SAMPLE BOOKS", "20.00", "", "70.00")
    statement = read_pdf(draw([cells]))
    assert [r.description for r in statement.rows] == ["EXAMPLE CAFE", "SAMPLE BOOKS"] and statement.dropped == []


def test_a_wrapped_line_far_from_its_date_joins_its_row_and_a_stray_figure_is_reported() -> None:
    cells: list[Cell] = [*HEADER, (110, 745, "Opening balance"), (0, 745, "500.00", 530)]
    cells += money_row(700, "02/11/2026", "EXAMPLE", "10.00", "", "490.00")
    cells += [(110, 676, "CAFE BRANCH"), (0, 676, "", 370)]  # more words for the row above, printed low
    cells += money_row(640, "03/11/2026", "SAMPLE BOOKS", "20.00", "", "470.00")
    cells += [(0, 560, "99.00", 370)]  # a figure no row can take
    statement = read_pdf(draw([cells]))
    assert [r.description for r in statement.rows] == ["EXAMPLE CAFE BRANCH", "SAMPLE BOOKS"]
    assert [(d.reason, d.amount) for d in statement.dropped] == [("unattached_line", Decimal("99.00"))]


# -- a Saudi bank profile (synthetic, unverified) ------------------------------------------------------------


def test_a_saudi_style_pdf_with_arabic_headers_and_a_hijri_column_is_read() -> None:
    cells: list[Cell] = [(400, 800, "كشف حساب"), (40, 760, "التاريخ"), (100, 760, "التاريخ الهجري"), (170, 760, "الوصف"), (330, 760, "مدين"),
                         (410, 760, "دائن"), (490, 760, "الرصيد"), (170, 745, "الرصيد الافتتاحي"), (0, 745, "1,000.00", 530)]
    cells += [(40, 730, "02/11/2026"), (100, 730, "11/05/1448"), (170, 730, "شراء مقهى تجريبي"), (0, 730, "40.00", 370), (0, 730, "960.00", 530)]
    cells += [(40, 716, "03/11/2026"), (100, 716, "12/05/1448"), (170, 716, "راتب شركة نموذجية"), (0, 716, "3,000.00", 450),
              (0, 716, "3,960.00", 530)]
    cells += [(170, 702, "الرصيد الختامي"), (0, 702, "3,960.00", 530)]
    statement = read_pdf(draw([cells]))
    assert [c.role for c in statement.columns][:2] == ["date", "skip"]
    assert [(r.posted_on, r.amount) for r in statement.rows] == [(date(2026, 11, 2), Decimal("-40.00")), (date(2026, 11, 3), Decimal("3000.00"))]
    assert statement.kind == "account" and statement.check.status == "matched"


def test_a_saudi_style_csv_is_detected_and_its_hijri_column_ignored() -> None:
    from anum_api.finance import csv_import

    content = ("التاريخ,التاريخ الهجري,الوصف,مدين,دائن,الرصيد\n"
               "02/11/2026,11/05/1448,شراء مقهى تجريبي,40.00,,960.00\n"
               "13/11/2026,22/05/1448,راتب شركة نموذجية,,3000.00,3960.00\n")
    parsed = csv_import.parse_file(content, account_id="acc", currency="SAR")
    assert parsed.preset == "saudi_generic"
    assert [(r.posted_on, r.amount) for r in parsed.rows] == [(date(2026, 11, 2), Decimal("-40.00")), (date(2026, 11, 13), Decimal("3000.00"))]
    assert "unverified" in csv_import.PRESETS["saudi_generic"].label.lower()
