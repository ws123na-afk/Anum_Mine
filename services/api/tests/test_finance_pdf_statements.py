"""PDF bank statement import (docs/finance.md#pdf-statements), in-memory stores.

Every statement here is SYNTHETIC: generated in the test with the repository's own PDF writer, labelled,
and not the layout of any real bank. ``test_postgres_finance_pdf.py`` repeats the import under RLS.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader, PdfWriter

from anum_api import main
from anum_api.finance import pdf_statement as P
from anum_api.finance import sms_service as svc
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.pdf_writer import PdfDocument
from anum_api.dependencies import memory_repository
from anum_api.vault.ocr_client import OcrUnavailable

TENANT, WORKSPACE = "tenant_pdf", "workspace_pdf"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_pdf", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_pdf", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_pdf", "x-user-roles": "viewer"}
API = "/api/v1/finance"
SENDER = "TestBank"
PASSWORD = "example"  # a plainly fake fixture password
SALARY_SMS = "SYNTHETIC. Salary of SAR 12,000.00 credited to your account ***5678 on 01/11/2026. Available balance SAR 14,000.00"
PURCHASE_SMS = "SYNTHETIC. Purchase of SAR 45.50 at CARREFOUR RIYADH with card **1234 on 03/11/2026 14:22. Ref 88AB12"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    svc._last_notified.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()


# -- synthetic statements ----------------------------------------------------------------------

HEADER = [(40, "Date"), (110, "Description"), (330, "Debit"), (410, "Credit"), (490, "Balance")]


def row(when: str, text: str, debit: str = "", credit: str = "", balance: str = "") -> list[tuple]:
    # Money right-aligned under its column, as banks print it.
    return [(40, when), (110, text), (300, debit, 60), (380, credit, 60), (460, balance, 70)]


def make_pdf(pages: list[list[list[tuple]]]) -> bytes:
    document = PdfDocument("SYNTHETIC statement")
    for number, page in enumerate(pages):
        if number:
            document._new_page()
        y = 790.0
        for cells in page:
            for x, value, *width in cells:
                if value:
                    if width:
                        document.page.text(x, y, value, 9, align="right", width=width[0])
                    else:
                        document.page.text(x, y, value, 9)
            y -= 14
    return document.render()


def statement(closing: str = "13,879.50") -> bytes:
    """Two pages: an opening balance, a wrapped description, a page footer, carried-forward lines."""
    return make_pdf([
        [
            [(40, "SYNTHETIC Example Bank. Account statement")],
            [(40, "Account number: 0123456789015678")],
            [(40, "Statement period: 01/11/2026 to 30/11/2026")],
            [(40, "Currency: SAR")],
            HEADER,
            row("", "Opening balance", "", "", "2,000.00"),
            row("01/11/2026", "SALARY NOVEMBER", "", "12,000.00", "14,000.00"),
            row("02/11/2026", "POS PURCHASE PANDA", "120.50", "", "13,879.50"),
            [(110, "CARD 4567XXXXXXXX1234 RIYADH")],
            row("", "Balance carried forward", "", "", "13,879.50"),
            [(40, "Page 1 of 2")],
        ],
        [
            HEADER,
            row("", "Balance brought forward", "", "", "13,879.50"),
            row("04/11/2026", "STC BILL Ref 77AA01", "300.00", "", "13,579.50"),
            row("05/11/2026", "REFUND NOON", "", "300.00", "13,879.50"),
            row("", "Closing balance", "", "", closing),
            [(40, "Page 2 of 2")],
        ],
    ])


def encrypt(data: bytes, password: str = PASSWORD) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def scanned_pdf(pages: int = 1) -> bytes:
    images = [Image.new("RGB", (400, 300), "white") for _ in range(pages)]
    out = io.BytesIO()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


# -- API helpers -------------------------------------------------------------------------------


def account(headers: dict = OWNER, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def read(account_id: str, data: bytes, headers: dict = OWNER, password: str | None = None, **params: str):  # type: ignore[no-untyped-def]
    extra = {"X-Statement-Password": password} if password is not None else {}
    return client.post(f"{API}/imports/pdf/read", headers={**headers, **extra, "content-type": "application/pdf"},
                       params={"account_id": account_id, "filename": "nov.pdf", **params}, content=data)


def reviewed(view: dict) -> list[dict[str, Any]]:
    return [{"posted_on": r["posted_on"], "description": r["description"], "amount": r["amount"], "balance": r["balance"],
             "reference": r["reference"]} for r in view["rows"]]


def import_rows(view: dict, rows: list[dict] | None = None, headers: dict = OWNER, **extra: Any):  # type: ignore[no-untyped-def]
    body = {"account_id": view["account_id"], "filename": view["filename"], "file_sha256": view["file_sha256"], "source": view["source"],
            "rows": rows if rows is not None else reviewed(view), "opening_balance": view["check"]["opening"],
            "closing_balance": view["check"]["closing"], **extra}
    return client.post(f"{API}/imports/pdf", headers=headers, json=body)


def sms_setup() -> dict[str, Any]:
    bank = account(name="Current", last4="5678")
    card = account(name="Card", kind="card", last4="1234")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    put = client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"], "credit_limit": "5000"}]})
    assert put.status_code == 200, put.text
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    return {"bank": bank, "card": card, "token": device.json()["token"]}


def send_sms(token: str, message: str) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text
    return response.json()


def error_type(response: Any) -> str:
    return response.json()["error"]["details"][0]["type"]


# -- reading -----------------------------------------------------------------------------------


def test_a_text_pdf_is_read_into_rows_with_period_balances_and_a_matching_check() -> None:
    bank = account(last4="5678")
    response = read(bank["id"], statement())
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["source"] == "text" and view["layout"] == "table" and view["page_count"] == 2
    assert view["roles"] == ["date", "description", "debit", "credit", "balance"]
    assert (view["period_start"], view["period_end"]) == ("2026-11-01", "2026-11-30")
    assert view["account_last4"] == "5678" and view["account_last4_matches"] is True
    assert view["statement_currency"] == "SAR" and view["warnings"] == []
    assert [(r["posted_on"], r["amount"]) for r in view["rows"]] == [
        ("2026-11-01", "12000.00"), ("2026-11-02", "-120.50"), ("2026-11-04", "-300.00"), ("2026-11-05", "300.00"),
    ]
    # The wrapped line joins the description; the card number in it is masked.
    assert view["rows"][1]["description"] == "POS PURCHASE PANDA CARD ***1234 RIYADH"
    assert view["rows"][2]["reference"] == "77AA01"
    check = view["check"]
    assert check["status"] == "matched" and check["opening"] == "2000.00" and check["closing"] == "13879.50"
    assert check["opening_source"] == check["closing_source"] == "statement" and check["breaks"] == []
    # The full account number is never returned, only its last four digits.
    assert "0123456789015678" not in response.text and "4567XXXXXXXX1234" not in response.text


def test_a_balance_mismatch_is_reported_and_import_needs_confirmation() -> None:
    bank = account(last4="5678")
    view = read(bank["id"], statement(closing="13,900.00")).json()
    check = view["check"]
    assert check["status"] == "mismatch" and check["difference"] == "20.50" and check["expected_closing"] == "13879.50"
    refused = import_rows(view)
    assert refused.status_code == 422 and error_type(refused) == "pdf_balance_mismatch"
    assert client.get(f"{API}/transactions", headers=OWNER).json()["items"] == []
    accepted = import_rows(view, accept_mismatch=True)
    assert accepted.status_code == 201 and accepted.json()["imported_count"] == 4


def test_an_edited_row_that_breaks_the_balance_is_refused_until_confirmed() -> None:
    bank = account(last4="5678")
    view = read(bank["id"], statement()).json()
    rows = reviewed(view)
    rows[1]["amount"] = "-12.50"  # a typo while editing
    assert error_type(import_rows(view, rows)) == "pdf_balance_mismatch"
    # Dropping a row breaks it too; the owner must say so.
    assert error_type(import_rows(view, reviewed(view)[:-1])) == "pdf_balance_mismatch"


def test_import_goes_through_the_csv_path_and_undo_removes_it() -> None:
    bank = account(last4="5678")
    view = read(bank["id"], statement()).json()
    response = import_rows(view)
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["imported_count"] == 4 and imported["preset"] == "pdf" and imported["filename"] == "nov.pdf"
    assert imported["date_format"] == "pdf" and imported["reconciled"] == 0
    items = client.get(f"{API}/transactions", headers=OWNER).json()["items"]
    assert sorted(Decimal(t["amount"]) for t in items) == [Decimal("-300.00"), Decimal("-120.50"), Decimal("300.00"), Decimal("12000.00")]
    # Every row is journalled as a statement entry.
    assert {e.source for e in sms_memory_store.entries.values()} == {"statement"}
    # Reading the same PDF again marks every row as already imported, and importing it adds nothing.
    again = read(bank["id"], statement()).json()
    assert again["duplicate_count"] == 4 and all(r["duplicate"] for r in again["rows"])
    assert import_rows(again).json()["imported_count"] == 0
    # The same rows as a CSV are duplicates too: one hash for both formats.
    csv = "Date,Description,Amount\n2026-11-05,REFUND NOON,300.00\n"
    assert client.post(f"{API}/imports", headers=OWNER, json={"account_id": bank["id"], "content": csv}).json()["imported_count"] == 0
    assert client.delete(f"{API}/imports/{imported['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/transactions", headers=OWNER).json()["items"] == []


def test_rows_already_recorded_from_sms_are_attached_not_added() -> None:
    s = sms_setup()
    assert send_sms(s["token"], SALARY_SMS)["status"] == "posted"
    view = read(s["bank"]["id"], statement()).json()
    imported = import_rows(view).json()
    assert imported["reconciled"] == 1 and imported["imported_count"] == 3 and imported["held_for_review"] == 0
    salary = next(e for e in sms_memory_store.entries.values() if e.kind == "salary")
    assert salary.reconciled_at is not None
    salaries = [t for t in client.get(f"{API}/transactions", headers=OWNER).json()["items"] if Decimal(t["amount"]) == Decimal("12000.00")]
    assert len(salaries) == 1  # never double counted


def test_an_agreeing_bank_reference_attaches_a_row_whose_words_differ() -> None:
    s = sms_setup()
    assert send_sms(s["token"], PURCHASE_SMS)["status"] == "posted"
    pdf = make_pdf([[
        HEADER, row("", "Opening balance", "", "", "1,000.00"),
        row("03/11/2026", "CARD TXN 9912 Ref 88AB12", "45.50", "", "954.50"),
        row("", "Closing balance", "", "", "954.50"),
    ]])
    view = read(s["card"]["id"], pdf).json()
    assert view["rows"][0]["reference"] == "88AB12"
    imported = import_rows(view).json()
    assert imported["reconciled"] == 1 and imported["held_for_review"] == 0 and imported["imported_count"] == 0
    # Without the reference the same row could be any payment of that amount: it waits for review.
    finance_store.clear()
    sms_memory_store.clear()
    s = sms_setup()
    send_sms(s["token"], PURCHASE_SMS)
    view = read(s["card"]["id"], pdf).json()
    rows = reviewed(view)
    rows[0]["reference"], rows[0]["description"] = None, "CARD TXN 9912"
    held = import_rows(view, rows).json()
    assert held["held_for_review"] == 1 and held["imported_count"] == 0


def test_a_password_protected_pdf_asks_for_the_password_and_never_keeps_it(caplog: pytest.LogCaptureFixture) -> None:
    bank = account(last4="5678")
    data = encrypt(statement())
    caplog.set_level(logging.DEBUG)
    missing = read(bank["id"], data)
    assert missing.status_code == 422 and error_type(missing) == "pdf_password_required"
    wrong = read(bank["id"], data, password="not-it")
    assert wrong.status_code == 422 and error_type(wrong) == "pdf_password_wrong"
    opened = read(bank["id"], data, password=PASSWORD)
    assert opened.status_code == 200 and len(opened.json()["rows"]) == 4
    assert PASSWORD not in opened.text and "not-it" not in wrong.text
    # Neither the logs nor the audit rows ever hold the password, an amount or a description.
    assert PASSWORD not in caplog.text and "not-it" not in caplog.text and "SALARY" not in caplog.text
    audits = [r for r in memory_repository.store.audit_records if r.action == "finance.import.pdf_read"]
    assert [a.metadata.get("outcome") for a in audits] == ["password_required", "password_wrong", "read"]
    for record in audits:
        text = repr(record)
        assert PASSWORD not in text and "12000" not in text and "SALARY" not in text


def test_a_scanned_pdf_is_read_through_the_vault_ocr_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    bank = account(last4="5678")
    seen: list[str] = []

    async def fake_ocr(data: bytes, content_type: str) -> list[str]:
        seen.append(content_type)
        return [
            "SYNTHETIC Example Bank",
            "Account No. XXXXXXXXXXXX5678",
            "Opening balance 500.00",
            "٠١/١١/٢٠٢٦ SALARY NOVEMBER ١٢٬٠٠٠٫٠٠ ١٢٬٥٠٠٫٠٠",
            "02/11/2026 ATM WITHDRAWAL 200.00 12,300.00",
            "03/11/2026 FEES 10.00 DR 12,290.00",
            "Closing balance 12,290.00",
        ]

    monkeypatch.setattr(P, "ocr_page", fake_ocr)
    view = read(bank["id"], scanned_pdf()).json()
    assert seen == ["image/jpeg"] and view["source"] == "ocr" and view["layout"] == "lines"
    assert [(r["posted_on"], r["amount"]) for r in view["rows"]] == [
        ("2026-11-01", "12000.00"), ("2026-11-02", "-200.00"), ("2026-11-03", "-10.00"),
    ]
    assert "sign_from_balance" in view["rows"][0]["flags"]
    assert view["check"]["status"] == "matched" and view["account_last4"] == "5678"
    imported = import_rows(view).json()
    assert imported["preset"] == "pdf_scan" and imported["imported_count"] == 3


def test_a_scan_without_ocr_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    bank = account()

    async def down(data: bytes, content_type: str) -> list[str]:
        raise OcrUnavailable("OCR is not connected on this server.")

    monkeypatch.setattr(P, "ocr_page", down)
    response = read(bank["id"], scanned_pdf())
    assert response.status_code == 503 and error_type(response) == "pdf_ocr_unavailable"


def test_the_owner_can_fix_the_column_mapping() -> None:
    bank = account(last4="5678")
    view = read(bank["id"], statement(), roles="date,description,credit,debit,balance").json()
    assert view["roles"] == ["date", "description", "credit", "debit", "balance"]
    assert view["rows"][0]["amount"] == "-12000.00" and view["check"]["status"] == "mismatch"
    bad = read(bank["id"], statement(), roles="date,date,debit,credit,balance")
    assert bad.status_code == 422 and error_type(bad) == "pdf_bad_mapping"
    assert read(bank["id"], statement(), roles="date,nonsense").status_code == 422


def test_refusals_permissions_and_caps() -> None:
    bank = account()
    assert read(bank["id"], statement(), headers=VIEWER).status_code == 403
    view = read(bank["id"], statement()).json()
    assert import_rows(view, headers=VIEWER).status_code == 403
    # Someone else's account is not found, for reading and for importing.
    assert read(bank["id"], statement(), headers=MEMBER).status_code == 404
    assert import_rows(view, headers=MEMBER).status_code == 404
    not_pdf = read(bank["id"], b"Date,Amount\n")
    assert not_pdf.status_code == 422 and error_type(not_pdf) == "pdf_not_pdf"
    damaged = read(bank["id"], b"%PDF-1.4\n garbage")
    assert damaged.status_code == 422 and error_type(damaged) == "pdf_damaged"
    assert read(bank["id"], b"%PDF-" + b"0" * (P.MAX_PDF_BYTES + 1)).status_code == 413
    zero = reviewed(view)
    zero[0]["amount"] = "0"
    assert import_rows(view, zero, accept_mismatch=True).status_code == 422
    assert import_rows(view, [{**reviewed(view)[0], "category": "x"}]).status_code == 422  # unknown field


# -- the reader, pure --------------------------------------------------------------------------


def lines(*text: str) -> list[list[str]]:
    return [list(text)]


def test_arabic_headers_and_arabic_indic_digits_are_read() -> None:
    page = lines(
        "كشف حساب SYNTHETIC",
        "رقم الحساب: SA44 2000 0001 2345 6789 1234",
        "التاريخ        البيان                    مدين          دائن          الرصيد",
        "الرصيد الافتتاحي                                                  ١٬٠٠٠٫٠٠",
        "٠٢/١١/٢٠٢٦     مشتريات بنده                ١٢٠٫٥٠                        ٨٧٩٫٥٠",
        "٠٣/١١/٢٠٢٦     راتب                                      ٥٬٠٠٠٫٠٠      ٥٬٨٧٩٫٥٠",
        "الرصيد الختامي                                                    ٥٬٨٧٩٫٥٠",
    )
    parsed = P.parse(page, currency="SAR")
    assert [c.role for c in parsed.columns] == ["date", "description", "debit", "credit", "balance"]
    assert [(str(r.posted_on), r.amount) for r in parsed.rows] == [("2026-11-02", Decimal("-120.50")), ("2026-11-03", Decimal("5000.00"))]
    assert parsed.rows[0].description == "مشتريات بنده"
    assert parsed.account_last4 == "1234" and parsed.check.status == "matched"


@pytest.mark.parametrize(
    ("opening", "raw", "expected"),
    [
        ("1,000.00", "05 Nov 2026  COFFEE  12.00 DR  988.00 CR", ("2026-11-05", Decimal("-12.00"))),
        ("1,000.00", "2026-11-05  COFFEE  -12.00  988.00", ("2026-11-05", Decimal("-12.00"))),
        ("1,000.00", "05-Nov-26  COFFEE  (12.00)  988.00", ("2026-11-05", Decimal("-12.00"))),
        ("1.000,00", "05.11.2026  COFFEE  1.012,00  2.012,00", ("2026-11-05", Decimal("1012.00"))),
    ],
)
def test_date_formats_both_decimal_marks_and_cr_dr_markers(opening: str, raw: str, expected: tuple[str, Decimal]) -> None:
    parsed = P.parse(lines(f"Opening balance {opening}", raw), currency="AED", positioned=False)
    first = parsed.rows[0]
    assert (str(first.posted_on), first.amount) == expected
    assert parsed.check.status == "matched"


def test_newest_first_statements_are_put_in_time_order() -> None:
    parsed = P.parse(lines(
        "Date        Description        Amount        Balance",
        "03/11/2026  RENT               -3,000.00     1,000.00",
        "02/11/2026  SALARY              4,000.00 CR  4,000.00",
    ), currency="AED")
    assert [r.description for r in parsed.rows] == ["SALARY", "RENT"]
    assert parsed.check.status == "matched" and parsed.check.opening == Decimal("0.00")
    # Without the CR the first row's sign is a guess: rows that add up then prove nothing.
    unsure = P.parse(lines(
        "Date        Description        Amount        Balance",
        "03/11/2026  RENT               -3,000.00     1,000.00",
        "02/11/2026  SALARY              4,000.00     4,000.00",
    ), currency="AED")
    assert "sign_unsure" in unsure.rows[0].flags and unsure.check.status == "unknown" and unsure.check.opening is None


def test_a_missing_row_shows_as_a_balance_break() -> None:
    parsed = P.parse(lines(
        "Date        Description        Debit        Credit        Balance",
        "Opening balance                                            100.00",
        "01/11/2026  SHOP               10.00                       90.00",
        "03/11/2026  SHOP               10.00                       60.00",
        "Closing balance                                            60.00",
    ), currency="AED")
    assert parsed.check.status == "mismatch" and parsed.check.breaks == [2] and parsed.check.difference == Decimal("-20.00")


def test_without_a_sign_or_balance_a_row_is_flagged_for_the_owner() -> None:
    parsed = P.parse(lines("05/11/2026 MYSTERY 25.00"), currency="AED", positioned=False)
    assert parsed.rows[0].amount == Decimal("-25.00") and "sign_unsure" in parsed.rows[0].flags
    assert parsed.check.status == "unknown"


def test_rows_without_a_year_take_it_from_the_period() -> None:
    parsed = P.parse(lines(
        "Statement period 15/12/2026 to 14/01/2027",
        "Date    Description     Amount     Balance",
        "20/12   GIFT            -50.00     950.00",
        "05/01   SALARY          1,000.00   1,950.00",
    ), currency="AED")
    assert [str(r.posted_on) for r in parsed.rows] == ["2026-12-20", "2027-01-05"]


def test_three_decimal_currencies_read_three_places() -> None:
    parsed = P.parse(lines("Date  Description  Amount  Balance", "01/11/2026  FEE  -1.250  98.750"), currency="KWD")
    assert parsed.rows[0].amount == Decimal("-1.250")


def test_numbers_in_descriptions_are_masked() -> None:
    assert P.mask_numbers("TRANSFER TO 1234567890123456") == "TRANSFER TO ***3456"
    assert P.mask_numbers("CARD 4567XXXXXXXX1234 SHOP") == "CARD ***1234 SHOP"


def test_page_limits_and_scan_limits() -> None:
    with pytest.raises(P.PdfStatementError) as raised:
        P.extract(scanned_pdf(P.MAX_OCR_PAGES + 1))
    assert raised.value.code == "too_many_pages"
    extracted = P.extract(scanned_pdf(2))
    assert extracted.source == "ocr" and len(extracted.images) == 2


def test_the_ocr_seam_reports_the_service_reason() -> None:
    async def down(data: bytes, content_type: str) -> list[str]:
        raise OcrUnavailable("The OCR service is busy. Try again in a minute.")

    with pytest.raises(P.PdfStatementError) as raised:
        asyncio.run(P.read_scanned([P.PageImage(b"x", "image/png")], down))
    assert raised.value.code == "ocr_unavailable" and "busy" in raised.value.message
