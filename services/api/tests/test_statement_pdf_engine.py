"""The PDF statement engine layer by layer (docs/finance-statements.md), on generated PDFs.

Every statement here is SYNTHETIC: drawn in the test with the repository's own PDF writer, with invented
names, numbers and merchants. Two fixtures mimic the layouts of real Gulf statements the owner checked
locally (a credit card statement with a stacked Arabic and English header, and a current account with
a summary page and three-line rows); no real statement is in the repository.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from datetime import date, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader, PdfWriter

from anum_api import main
from anum_api.finance import statement_pdf as S
from anum_api.finance.pdf_templates import template_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.statement_pdf import columns as C
from anum_api.finance.statement_pdf import dates as D
from anum_api.finance.statement_pdf import meta as M
from anum_api.finance.statement_pdf import ocr as OCR
from anum_api.finance.statement_pdf.words import Word, logical_order, reorder_rtl
from anum_api.finance.store import finance_store
from anum_api.pdf_writer import PdfDocument

client = TestClient(main.app)
OWNER = {"x-tenant-id": "tenant_engine", "x-workspace-id": "workspace_engine", "x-user-id": "owner_engine", "x-user-roles": "owner"}
SOMEONE = {**OWNER, "x-user-id": "member_engine", "x-user-roles": "member"}
API = "/api/v1/finance"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    template_memory_store.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()
    template_memory_store.clear()


# -- drawing synthetic statements ----------------------------------------------------------------

Cell = tuple  # (x, y, text) or (x, y, text, right_edge) for right-aligned figures


def draw(pages: list[list[Cell]], *, sideways: bool = False) -> bytes:
    document = PdfDocument("SYNTHETIC")
    for number, cells in enumerate(pages):
        if number:
            document._new_page()
        page = document.page
        for cell in cells:
            x, y, text = cell[0], cell[1], cell[2]
            if not text:
                continue
            if len(cell) > 3:
                page.text(cell[3] - 200, y, text, 8, align="right", width=200)
            else:
                page.text(x, y, text, 8)
        if sideways:
            # The whole page's text drawn turned a quarter (a landscape statement stored sideways).
            page.ops.insert(0, "q 0 1 -1 0 595 0 cm")
            page.ops.append("Q")
    return document.render()


def read(data: bytes, currency: str = "SAR", **options: object) -> S.Statement:
    extracted = S.extract(data)
    return S.parse(extracted.pages, currency=currency, page_widths=extracted.widths, **options)  # type: ignore[arg-type]


def card_statement(*, example_page: bool = True, due: str = "1,232.47") -> bytes:
    """A credit card statement: card facts in label boxes, a stacked Arabic over English header, a card
    holder band, foreign spends with currency, rate, fee and VAT, a wrapped description whose figures
    sit a little lower than its date, an annual fee row, a summary box and a page of bank examples."""
    p1: list[Cell] = [
        (380, 800, "كشف حساب بطاقة الائتمان"), (380, 788, "SYNTHETIC Credit Card Statement"),
        (300, 700, "تاريخ الكشف"), (420, 700, "تاريخ استحقاق الدفع"),
        (10, 692, "Account Number"), (300, 692, "Statement Date"), (420, 692, "Payment Due Date"),
        (10, 676, "00000000017"), (300, 676, "31/08/2026"), (420, 676, "25/09/2026"),
        # The header: Arabic labels above English ones, on slightly different baselines.
        (8, 600, "تاريخ المعاملة"), (205, 603, "مبلغ العملية الدولية"), (300, 603, "سعر تحويل العملات"), (380, 603, "الرسوم الأخرى"),
        (445, 600, "قيمة الضريبة"), (505, 600, "المبلغ بالريال السعودي"),
        (8, 590, "Trans. Date"), (60, 593, "Activity / Transaction"), (202, 593, "International Transaction"), (300, 593, "Currency"),
        (380, 593, "Other Fees"), (445, 590, "VAT Amount"), (505, 590, "Amount (Saudi Riyals)"),
        (220, 585, "Amount"), (300, 585, "Conversion Rate"), (385, 585, "Charges"),
        # The card holder band: a masked card number and a name, not a transaction.
        (225, 565, "XXXXXXXXXX001234"), (235, 553, "SYNTHETIC HOLDER"),
        (6, 535, "24/08/2026"), (53, 535, "EXAMPLE GROCER Riyadh"), (0, 535, "0.00", 261), (0, 535, "0.00", 335), (0, 535, "0.00", 425),
        (0, 535, "0.00", 487), (0, 535, "41.20", 555),
        (6, 521, "26/08/2026"), (53, 521, "EXAMPLE STORE DUBAI"), (0, 521, "300.00", 261), (273, 521, "AED"), (0, 521, "1.02", 335),
        (0, 521, "6.12", 425), (0, 521, "0.92", 487), (0, 521, "313.04", 555),
        # Its second description line is below, and its figures sit 3 points lower than its date.
        (6, 507, "27/08/2026"), (50, 507, "EXAMPLE AI *CHAT SUBSCR"), (0, 504, "200.00", 261), (273, 504, "USD"), (0, 504, "3.75", 335),
        (0, 504, "15.00", 425), (0, 504, "2.25", 487), (0, 504, "767.25", 555),
        (53, 496, "EXAMPLE.AI"),
        (6, 482, "29/08/2026"), (53, 482, "EXAMPLE PAY Riyadh"), (0, 482, "0.00", 261), (0, 482, "0.00", 335), (0, 482, "0.00", 425),
        (0, 482, "0.00", 487), (0, 482, "81.00", 555),
        (6, 468, "31/08/2026"), (53, 468, "Annual Fee + VAT"), (0, 468, "0.00", 261), (0, 468, "3.90", 487), (0, 468, "29.98", 555),
        # The summary box: labels above one line of figures.
        (7, 350, "الرصيد السابق"), (450, 350, "المبلغ الكامل المستحق"),
        (7, 340, "Previous Balance"), (70, 340, "(+) Purchases &"), (450, 340, "Total Amount Due"),
        (79, 332, "Advances"),
        (0, 316, "0.00", 40), (0, 316, "1,232.47", 111), (0, 316, due, 554),
    ]
    pages = [p1]
    if example_page:
        pages.append([
            (40, 800, "Illustrative Example"), (40, 780, "مثال توضيحي"),
            (40, 760, "Date"), (100, 760, "Description"), (300, 760, "Amount"),
            (40, 745, "08/01/2015"), (100, 745, "Forex Example"), (0, 745, "1,000.00", 330),
        ])
    return draw(pages)


def account_statement(*, closing: str = "SAR 1,250.42", withdrawals: str = "3") -> bytes:
    """A current account: a summary page (labels left, values in the middle, Arabic labels right), then
    rows of three lines: the type above the date, the time and note beside or under it, the other party
    under that; every amount followed by SAR; a transfer and its charge share a time."""
    summary: list[Cell] = [
        (187, 800, "كشف حساب جاري"), (151, 780, "SYNTHETIC Account Statement"), (6, 760, "CLICK TO VALIDATE"),
        (0, 745, "Ref. No"), (236, 745, "00000001"),
        (0, 700, "Opening Balance"), (0, 700, "100.00", 256), (259, 700, "SAR"), (440, 700, "رصيد الحساب الافتتاحي"),
        (0, 680, "Closing Balance"), (0, 680, closing.split()[1], 262), (264, 680, "SAR"), (463, 680, "رصيد الإقفال"),
        (0, 660, "Number Of Deposits"), (252, 660, "1"), (461, 660, "عدد الإيداعات"),
        (0, 640, "Number Of Withdrawals"), (250, 640, withdrawals), (457, 640, "عدد السحوبات"),
        (0, 620, "Total Deposits"), (0, 620, "1,500.00", 269), (271, 620, "SAR"), (449, 620, "إجمالي الإيداعات"),
        (0, 600, "Total Withdrawals"), (0, 600, "349.58", 269), (271, 600, "SAR"), (445, 600, "إجمالي السحوبات"),
        (0, 580, "On The Period"), (178, 580, "2026/05/04"), (254, 580, "-"), (287, 580, "2026/07/03"), (472, 580, "خلال الفترة"),
        (10, 550, "National Address"), (0, 530, "City"), (232, 530, "SYNTHETIC CITY"),
    ]
    header = [(25, 800, "Date"), (118, 800, "Transaction Details"), (279, 800, "Debit"), (368, 800, "Credit"), (454, 800, "Balance")]

    def row(y: float, when: str, kind: str, note_on_date: bool, note: str, party: str, debit: str, credit: str, balance: str) -> list[Cell]:
        cells: list[Cell] = [(71, y + 6, kind), (10, y, when), (0, y, debit, 289), (291, y, "SAR"), (0, y, credit, 379), (381, y, "SAR"),
                             (0, y, balance, 474), (476, y, "SAR")]
        if note_on_date:
            cells.append((71, y, note))
        else:
            cells.append((71, y - 7, note))
        cells.append((71, y - (10 if note_on_date else 17), party))
        return cells

    page2 = header + row(780, "2026/05/10", "Internal Transfer", False, "Time:21:09:04**Note:W-/FRACCT/00000001", "", "0.00", "1,500.00", "1,600.00")
    page2 += row(752, "2026/05/11", "Outward IPS Credit Transfer", True, "Time:07:46:19**Note:00000002/EXAMPLE", "EXAMPLE PERSON",
                 "300.00", "0.00", "1,300.00")
    page2 += row(724, "2026/05/11", "Outward IPS Credit Transaction Charges", True, "Time:07:46:19**Note:00000002/EXAMPLE",
                 "EXAMPLE PERSON", "0.58", "0.00", "1,299.42")
    page2 += [(254, 40, "2")]
    page3 = header + row(780, "2026/05/12", "POS purchase Atheer (Domestic)", True, "Time:23:19:46**Note:(00000003-01)",
                         "EXAMPLE SHOP, RIYADH, SA", "49.00", "0.00", "1,250.42")
    page3 += [(10, 150, "Note"), (10, 130, "This document is confidential and under the responsibility of the holder."), (254, 40, "3")]
    return draw([summary, page2, page3])


# -- layer 1: extraction -------------------------------------------------------------------------


def test_words_come_with_boxes_in_reading_orientation_and_arabic_in_reading_order() -> None:
    data = draw([[(40, 820, "SYNTHETIC Example Bank account statement"), (40, 800, "Date"), (0, 800, "120.50", 360), (110, 780, "مشتريات بنده")]])
    words = S.extract(data).pages[0]
    by_text = {w.text: w for w in words}
    assert by_text["Date"].x0 < by_text["120.50"].x0 and by_text["Date"].top < by_text["مشتريات"].top
    assert abs(by_text["120.50"].x1 - 320) < 1  # right edge where it was drawn (360 less the 40 point left margin)
    assert {"مشتريات", "بنده"} <= set(by_text)  # each Arabic word in reading order, presentation forms folded


def test_a_page_drawn_sideways_is_read_upright() -> None:
    upright = read(card_statement(example_page=False))
    sideways = read(draw([[(6, 560, "SYNTHETIC Example Bank account statement"), (6, 535, "24/08/2026"), (53, 535, "SHOP"),
                           (0, 535, "41.20", 555)]], sideways=True))
    assert len(upright.rows) == 5
    assert [r.description for r in sideways.rows] == ["SHOP"] and sideways.rows[0].amount == Decimal("-41.20")


def test_rtl_reordering_is_its_own_inverse_and_keeps_numbers() -> None:
    words = ["رقم", "الحساب:", "SA44", "2000", "1234"]
    assert reorder_rtl(reorder_rtl(words)) == words
    assert reorder_rtl(words) == ["SA44", "2000", "1234", "الحساب:", "رقم"]
    assert logical_order("ﻩﺪﻨﺑ 12") == "12 ﺑﻨﺪﻩ"


def test_a_password_protected_statement_needs_its_password() -> None:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(card_statement())))
    writer.encrypt("example", algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(S.PdfStatementError) as raised:
        S.extract(out.getvalue())
    assert raised.value.code == "password_required"
    opened = S.extract(out.getvalue(), "example")
    assert len(S.parse(opened.pages, currency="SAR", page_widths=opened.widths).rows) == 5


# -- layer 2: columns, rows and dates ------------------------------------------------------------


def test_a_card_statement_with_a_stacked_bilingual_header_is_read_row_by_row() -> None:
    statement = read(card_statement())
    assert statement.kind == "card" and statement.layout == "table"
    assert [c.role for c in statement.columns if c.role != "skip"] == ["date", "description", "foreign_amount", "fx_rate", "fee", "vat", "amount"]
    rows = statement.rows
    assert [str(r.posted_on) for r in rows] == ["2026-08-24", "2026-08-26", "2026-08-27", "2026-08-29", "2026-08-31"]
    assert [r.amount for r in rows] == [Decimal("-41.20"), Decimal("-313.04"), Decimal("-767.25"), Decimal("-81.00"), Decimal("-29.98")]
    # The wrapped description joins its row, and its figures (3 points lower) stay with it.
    assert rows[2].description == "EXAMPLE AI *CHAT SUBSCR EXAMPLE.AI"
    assert (rows[2].original_amount, rows[2].original_currency, rows[2].fx_rate, rows[2].fee, rows[2].vat) == (
        Decimal("200.00"), "USD", Decimal("3.75"), Decimal("15.00"), Decimal("2.25"))
    # Local spends carry no foreign details; the fee row only its VAT.
    assert rows[0].original_amount is None and rows[0].original_currency is None
    assert (rows[4].vat, rows[4].original_amount) == (Decimal("3.90"), None)
    assert sum(1 for r in rows if r.original_amount is not None) == 2
    # The card holder band is not a row; its card gives the last four digits.
    assert statement.account_last4 == "1234" and all("HOLDER" not in r.description for r in rows)
    assert (statement.statement_date, statement.due_date, statement.period_end) == (date(2026, 8, 31), date(2026, 9, 25), date(2026, 8, 31))


def test_a_card_statement_reconciles_against_its_amount_due_and_skips_the_example_page() -> None:
    statement = read(card_statement())
    assert statement.check.status == "matched"
    assert [(s.name, s.ok) for s in statement.check.summary] == [("total_due", True)]
    assert statement.skipped_pages == [(2, "not_transactions")]
    assert all(r.posted_on and r.posted_on.year == 2026 for r in statement.rows)
    wrong = read(card_statement(due="1,300.00"))
    assert wrong.check.status == "mismatch" and [(s.name, s.ok) for s in wrong.check.summary] == [("total_due", False)]


def test_an_account_statement_is_checked_against_its_own_summary() -> None:
    statement = read(account_statement())
    assert statement.kind == "account"
    rows = statement.rows
    assert len(rows) == 4 and all(r.page in (2, 3) for r in rows)  # nothing from the summary page or the footer
    assert [r.amount for r in rows] == [Decimal("1500.00"), Decimal("-300.00"), Decimal("-0.58"), Decimal("-49.00")]
    assert statement.check.status == "matched" and statement.check.breaks == []
    assert [(s.name, s.ok) for s in statement.check.summary] == [
        ("deposits_count", True), ("withdrawals_count", True), ("deposits_total", True), ("withdrawals_total", True)]
    assert (statement.period_start, statement.period_end) == (date(2026, 5, 4), date(2026, 7, 3))
    assert statement.date_format == "%Y-%m-%d"


def test_three_line_rows_split_into_type_time_and_other_party_and_charges_link_to_their_transfer() -> None:
    rows = read(account_statement()).rows
    transfer, charge, purchase = rows[1], rows[2], rows[3]
    assert (transfer.kind, transfer.time) == ("Outward IPS Credit Transfer", time(7, 46, 19))
    assert charge.kind == "Outward IPS Credit Transaction Charges" and charge.parent_line == transfer.line
    assert purchase.parent_line is None and purchase.counterparty == "EXAMPLE SHOP, RIYADH, SA"
    assert rows[0].kind == "Internal Transfer" and rows[0].time == time(21, 9, 4)  # its time line is under the date
    assert "***0001" in rows[0].description and "00000001" not in rows[0].description  # long numbers masked


def test_summary_counts_that_disagree_fail_the_check() -> None:
    statement = read(account_statement(withdrawals="4"))
    assert statement.check.status == "mismatch" and "summary_differs" in statement.warnings
    assert ("withdrawals_count", False) in [(s.name, s.ok) for s in statement.check.summary]
    closing = read(account_statement(closing="SAR 1,000.00"))
    assert closing.check.status == "mismatch" and closing.check.difference == Decimal("-250.42")


def test_columns_from_the_content_when_there_is_no_header() -> None:
    cells: list[Cell] = [(40, 800, "Opening balance"), (0, 800, "1,000.00", 520)]
    for i, (day, text, debit, credit, balance) in enumerate([
        ("01/11/2026", "SALARY", "", "5,000.00", "6,000.00"), ("02/11/2026", "RENT", "2,000.00", "", "4,000.00"),
        ("03/11/2026", "REFUND", "", "50.00", "4,050.00"), ("04/11/2026", "SHOP", "25.50", "", "4,024.50"),
    ]):
        y = 780 - i * 14
        cells += [(40, y, day), (110, y, text), (0, y, debit, 360), (0, y, credit, 440), (0, y, balance, 520)]
    statement = read(draw([cells]), "AED")
    assert statement.layout == "columns" and [c.role for c in statement.columns] == ["debit", "credit", "balance"]
    assert [r.amount for r in statement.rows] == [Decimal("5000.00"), Decimal("-2000.00"), Decimal("50.00"), Decimal("-25.50")]
    assert statement.check.status == "matched"


def test_debit_and_credit_headers_the_wrong_way_round_are_swapped_when_only_that_reconciles() -> None:
    cells: list[Cell] = [(40, 800, "Date"), (110, 800, "Description"), (330, 800, "Credit"), (410, 800, "Debit"), (490, 800, "Balance"),
                         (110, 786, "Opening balance"), (0, 786, "100.00", 530)]
    cells += [(40, 772, "01/11/2026"), (110, 772, "SHOP"), (0, 772, "10.00", 360), (0, 772, "90.00", 530)]
    cells += [(40, 758, "02/11/2026"), (110, 758, "PAY IN"), (0, 758, "60.00", 440), (0, 758, "150.00", 530)]
    statement = read(draw([cells]), "AED")
    assert [r.amount for r in statement.rows] == [Decimal("-10.00"), Decimal("60.00")]
    assert "debit_credit_swapped" in statement.warnings and all("columns_swapped" in r.flags for r in statement.rows)


def test_a_header_wrapped_over_two_lines_and_repeated_on_the_next_page() -> None:
    header: list[Cell] = [(40, 806, "Transaction"), (40, 798, "Date"), (110, 798, "Narration"), (330, 798, "Withdrawals"),
                          (410, 798, "Deposits"), (490, 798, "Balance")]
    p1 = header + [(110, 784, "Balance brought forward"), (0, 784, "500.00", 530),
                   (40, 770, "01/11/2026"), (110, 770, "SHOP ONE"), (0, 770, "100.00", 370), (0, 770, "400.00", 530),
                   (110, 756, "Balance carried forward"), (0, 756, "400.00", 530)]
    p2 = header + [(110, 784, "Balance brought forward"), (0, 784, "400.00", 530),
                   (40, 770, "05/11/2026"), (110, 770, "PAY IN"), (0, 770, "250.00", 450), (0, 770, "650.00", 530),
                   (110, 756, "Closing balance"), (0, 756, "650.00", 530)]
    statement = read(draw([p1, p2]), "AED")
    assert [c.role for c in statement.columns] == ["date", "description", "debit", "credit", "balance"]
    assert [r.amount for r in statement.rows] == [Decimal("-100.00"), Decimal("250.00")]
    assert statement.check.status == "matched" and statement.check.page_breaks == []


def test_a_page_whose_brought_forward_balance_does_not_follow_is_flagged() -> None:
    header: list[Cell] = [(40, 798, "Date"), (110, 798, "Description"), (330, 798, "Debit"), (410, 798, "Credit"), (490, 798, "Balance")]
    p1 = header + [(110, 784, "Opening balance"), (0, 784, "500.00", 530),
                   (40, 770, "01/11/2026"), (110, 770, "SHOP"), (0, 770, "100.00", 370), (0, 770, "400.00", 530)]
    p2 = header + [(110, 784, "Balance brought forward"), (0, 784, "380.00", 530),
                   (40, 770, "05/11/2026"), (110, 770, "SHOP"), (0, 770, "30.00", 370), (0, 770, "350.00", 530)]
    statement = read(draw([p1, p2]), "AED")
    assert statement.check.page_breaks == [2] and "page_breaks" in statement.warnings
    assert statement.check.status == "mismatch"


def test_dates_one_order_per_statement_hijri_and_arabic_months() -> None:
    assert D.decide_order(["01/02/2026", "13/02/2026"]) == ("dmy", False)
    assert D.decide_order(["02/13/2026", "02/01/2026"]) == ("mdy", False)
    assert D.decide_order(["01/02/2026", "03/04/2026"]) == ("dmy", True)  # every date fits both: day first, flagged
    assert D.to_date("2026/05/10", "dmy", None) == (date(2026, 5, 10), False)
    assert D.to_date("5 نوفمبر 2026", "dmy", None) == (date(2026, 11, 5), False)
    assert D.to_date("Nov 5, 2026", "dmy", None) == (date(2026, 11, 5), False)
    assert D.to_date("01/09/1445", "dmy", None) == (date(2024, 3, 11), True)  # 1 Ramadan 1445
    assert D.to_date("1 رمضان 1445", "dmy", None) == (date(2024, 3, 11), True)
    assert not D.plausible("0.00") and not D.plausible("45.13") and D.plausible("05.11")


def test_hijri_dated_rows_are_converted_and_marked_for_checking() -> None:
    cells: list[Cell] = [(40, 800, "Date"), (110, 800, "Description"), (330, 800, "Amount"), (410, 800, "Balance"),
                         (40, 786, "01/09/1445"), (110, 786, "SHOP"), (0, 786, "-10.00", 370), (0, 786, "90.00", 450),
                         (40, 772, "02/09/1445"), (110, 772, "SHOP"), (0, 772, "-5.00", 370), (0, 772, "85.00", 450)]
    statement = read(draw([cells]), "SAR")
    assert [str(r.posted_on) for r in statement.rows] == ["2024-03-11", "2024-03-12"]
    assert all("hijri_date" in r.flags for r in statement.rows) and all(r.confidence < 1 for r in statement.rows)


def test_amount_formats() -> None:
    money = S.money_pattern(2)
    for text, expected in [("1,234.50", "1234.50"), ("1.234,50", "1234.50"), ("(12.00)", "-12.00"), ("12.00 DR", "-12.00"),
                           ("12.00 CR", "12.00"), ("12.00-", "-12.00"), ("12.00 مدين", "-12.00")]:
        token = text.split()[0]
        assert C.amounts.is_money(token, money), text
        assert C.amounts.parse(text) == Decimal(expected), text
    assert not C.amounts.is_money("2026", money) and not C.amounts.is_money("88AB12", money)


def test_header_words_in_english_and_arabic() -> None:
    assert [C.role_of(x) for x in ("Trans. Date", "تاريخ المعاملة", "Narration", "البيان", "Withdrawals", "مدين", "Deposits", "دائن",
                                   "Running Balance", "الرصيد", "Ref No", "Currency", "Conversion Rate", "VAT Amount", "Other Fees",
                                   "International Transaction Amount", "Amount (Saudi Riyals)")] == [
        "date", "date", "description", "description", "debit", "debit", "credit", "credit", "balance", "balance", "reference", "currency",
        "fx_rate", "vat", "fee", "foreign_amount", "amount"]


def test_statement_facts_beside_and_under_their_labels() -> None:
    lines = C.lines_of(S.extract(account_statement()).pages[0])
    for line in lines:
        line.tokens = C.tokenize(line, S.money_pattern(2))
    found = M.facts(lines, S.money_pattern(2), "ymd")
    assert found.count("deposits_count") == 1 and found.money("deposits_total") == Decimal("1500.00")
    assert M.masked_card("XXXXXXXXXX001234") == "1234" and M.masked_card("484783******8747") == "8747"
    assert M.detail("POS purchase Time:23:19:46**Note:(0000-1) EXAMPLE SHOP, RIYADH, SA").counterparty == "EXAMPLE SHOP, RIYADH, SA"


# -- layer 3: scanned pages ----------------------------------------------------------------------


def _boxes(rows: list[list[tuple[int, str]]], top: int = 100) -> list[list[dict[str, float | str]]]:
    """OCR word boxes in pixels: one list per line, each word at its left edge."""
    out = []
    for n, row in enumerate(rows):
        out.append([{"text": text, "left": float(left), "top": float(top + n * 30), "width": float(len(text) * 12), "height": 20.0,
                     "confidence": 91.0} for left, text in row])
    return out


def scanned(pages: int = 1) -> bytes:
    out = io.BytesIO()
    images = [Image.new("RGB", (400, 300), "white") for _ in range(pages)]
    images[0].save(out, "PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


def test_a_scan_with_word_boxes_goes_through_the_same_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        [(10, "Date"), (200, "Description"), (600, "Debit"), (750, "Credit"), (900, "Balance")],
        [(200, "Opening"), (300, "balance"), (900, "1,000.00")],
        [(10, "01/11/2026"), (200, "SHOP"), (600, "120.50"), (900, "879.50")],
        [(10, "02/11/2026"), (200, "SALARY"), (750, "500.00"), (900, "1,379.50")],
        [(200, "Closing"), (300, "balance"), (900, "1,379.50")],
    ]

    async def fake(data: bytes, content_type: str, page: int) -> list[Word]:
        return OCR.words_from_ocr([" ".join(t for _, t in r) for r in rows], _boxes(rows), [91.0] * len(rows), page)

    monkeypatch.setattr(OCR, "ocr_page", fake)
    extracted = S.extract(scanned())
    pages = asyncio.run(S.read_scanned(extracted.images))
    statement = S.parse(pages, currency="AED", page_widths=[1200.0])
    assert statement.layout == "table" and [r.amount for r in statement.rows] == [Decimal("-120.50"), Decimal("500.00")]
    assert statement.check.status == "matched" and all(r.confidence <= 0.91 for r in statement.rows)


def test_a_picture_page_inside_a_text_pdf_is_read_by_ocr_and_skipped_when_it_is_an_example(monkeypatch: pytest.MonkeyPatch) -> None:
    text = PdfReader(io.BytesIO(card_statement(example_page=False)))
    picture = PdfReader(io.BytesIO(scanned()))
    writer = PdfWriter()
    writer.add_page(text.pages[0])
    writer.add_page(picture.pages[0])
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    assert list(S.extract(data).image_pages) == [2]

    async def example(data: bytes, content_type: str, page: int) -> list[Word]:
        rows = [[(10, "Illustrative"), (150, "Example")], [(10, "08/01/2015"), (200, "Forex"), (900, "1,000.00")]]
        return OCR.words_from_ocr([" ".join(t for _, t in r) for r in rows], _boxes(rows), [90.0, 90.0], page)

    monkeypatch.setattr(OCR, "ocr_page", example)
    account = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    view = client.post(f"{API}/imports/pdf/read", headers={**OWNER, "content-type": "application/pdf"},
                       params={"account_id": account["id"], "filename": "card.pdf"}, content=data).json()
    assert view["kind"] == "card" and len(view["rows"]) == 5
    assert view["skipped_pages"] == [{"page": 2, "reason": "not_transactions"}]
    assert view["check"]["status"] == "matched" and view["account_last4_matches"] is True


# -- layer 4 and 5 through the API: confidence, details, templates -------------------------------


def _account(headers: dict = OWNER, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _read(account_id: str, data: bytes, headers: dict = OWNER, **params: str):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/imports/pdf/read", headers={**headers, "content-type": "application/pdf"},
                       params={"account_id": account_id, "filename": "statement.pdf", **params}, content=data)


def test_the_api_returns_row_details_and_the_summary_checks() -> None:
    account = _account(last4="0001")
    view = _read(account["id"], account_statement()).json()
    assert view["kind"] == "account" and view["check"]["status"] == "matched"
    assert {c["name"] for c in view["check"]["summary"]} == {"deposits_count", "withdrawals_count", "deposits_total", "withdrawals_total"}
    charge = view["rows"][2]
    assert charge["parent_line"] == 2 and charge["time"] == "07:46:19" and charge["confidence"] == 1.0
    card = _read(_account(name="Card", kind="card", last4="1234")["id"], card_statement()).json()
    foreign = card["rows"][2]
    assert (foreign["original_amount"], foreign["original_currency"], foreign["fx_rate"], foreign["fee"], foreign["vat"]) == (
        "200.00", "USD", "3.75", "15.00", "2.25")
    assert card["statement_date"] == "2026-08-31" and card["due_date"] == "2026-09-25"


def _misread() -> bytes:
    """Debit and credit headers with no words ANUM knows: the owner says which is which."""
    cells: list[Cell] = [(40, 800, "Date"), (110, 800, "Description"), (330, 800, "Out"), (410, 800, "In"), (490, 800, "Balance"),
                         (110, 786, "Opening balance"), (0, 786, "100.00", 530),
                         (40, 772, "01/11/2026"), (110, 772, "SHOP"), (0, 772, "10.00", 360), (0, 772, "90.00", 530)]
    return draw([cells])


def test_a_corrected_mapping_is_remembered_for_the_next_statement_of_that_layout_and_can_be_forgotten() -> None:
    account = _account()
    first = _read(account["id"], _misread()).json()
    # Column names ANUM does not know ("Out", "In") are left for the owner to name.
    assert first["layout"] == "table" and first["roles"] == ["date", "description", "skip", "skip", "balance"] and first["template_id"] is None
    table = _read(account["id"], card_statement(example_page=False)).json()
    roles = table["roles"]
    assert roles == ["date", "description", "foreign_amount", "fx_rate", "fee", "vat", "amount"]
    # The owner overrides a role and imports with "remember".
    fixed = list(roles)
    fixed[4] = "skip"
    corrected = _read(account["id"], card_statement(example_page=False), roles=",".join(fixed)).json()
    assert corrected["roles"] == fixed
    body = {"account_id": account["id"], "filename": "card.pdf", "file_sha256": corrected["file_sha256"], "source": "text",
            "rows": [{"posted_on": r["posted_on"], "description": r["description"], "amount": r["amount"], "time": r["time"]}
                     for r in corrected["rows"]],
            "opening_balance": corrected["check"]["opening"], "closing_balance": corrected["check"]["closing"],
            "layout": {"fingerprint": corrected["fingerprint"], "labels": [c["label"] for c in corrected["columns"]], "roles": fixed,
                       "date_format": None, "remember": True}}
    imported = client.post(f"{API}/imports/pdf", headers=OWNER, json=body)
    assert imported.status_code == 201, imported.text
    templates = client.get(f"{API}/imports/pdf/templates", headers=OWNER).json()
    assert len(templates) == 1 and templates[0]["roles"] == fixed and templates[0]["uses"] == 1
    # The next statement with that layout is read with the owner's mapping.
    again = _read(account["id"], card_statement(example_page=False)).json()
    assert again["template_id"] == templates[0]["id"] and again["roles"] == fixed
    # Nobody else sees or uses it.
    other = _account(SOMEONE)
    assert client.get(f"{API}/imports/pdf/templates", headers=SOMEONE).json() == []
    assert _read(other["id"], card_statement(example_page=False), SOMEONE).json()["template_id"] is None
    assert client.delete(f"{API}/imports/pdf/templates/{templates[0]['id']}", headers=SOMEONE).status_code == 404
    assert client.delete(f"{API}/imports/pdf/templates/{templates[0]['id']}", headers=OWNER).status_code == 204
    assert _read(account["id"], card_statement(example_page=False)).json()["template_id"] is None


def test_a_foreign_currency_row_is_refused_at_import() -> None:
    account = _account()
    body = {"account_id": account["id"], "filename": "x.pdf", "file_sha256": "a" * 64, "source": "text",
            "rows": [{"posted_on": "2026-11-01", "description": "SHOP", "amount": "-10.00", "currency": "USD"}]}
    refused = client.post(f"{API}/imports/pdf", headers=OWNER, json=body)
    assert refused.status_code == 422 and "USD" in refused.text
