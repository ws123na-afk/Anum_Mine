"""Contacts import from Excel (.xlsx) and vCard (.vcf) files (docs/contacts.md#excel-and-vcard-files).

The workbooks are built in the test with ``zipfile`` so each hostile shape (macros, a formula, sparse cells, an
old .xls, a zip bomb) is exact. SYNTHETIC data only.
"""

from __future__ import annotations

import base64
import io
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.contacts.store import memory_data

TENANT, WORKSPACE = "tenant_contacts_files", "workspace_contacts_files"
URL = "/api/v1/contacts"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_cf", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_cf", "x-user-roles": "member"}
client = TestClient(main.app)

_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="People" sheetId="1" r:id="rId1"/><sheet name="Other" sheetId="2" r:id="rId2"/></sheets></workbook>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
    '</Relationships>'
)
_STRINGS = ["Name", "Company", "Mobile", "Email", "Sara Ahmed", "Tyre Co", "sara@files.example", "Omar Khalid", "Do not contact", "email"]


def _sheet(rows: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>')


def _strings(values: list[str]) -> str:
    items = "".join(f"<si><t>{v}</t></si>" for v in values)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(values)}">{items}</sst>')


# Row 1 header; row 2 Sara with a phone stored as a number and a formula cell; a blank row 3; row 4 Omar with
# the company cell left out (sparse) and his email in column D; row 5 a phone typed as text with a do-not-contact.
PEOPLE = _sheet(
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c>'
    '<c r="E1" t="s"><v>8</v></c></row>'
    '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" t="s"><v>5</v></c><c r="C2"><v>971501234567</v></c>'
    '<c r="D2" t="str"><f>LOWER("SARA@FILES.EXAMPLE")</f><v>sara@files.example</v></c></row>'
    '<row r="3"></row>'
    '<row r="4"><c r="A4" t="s"><v>7</v></c><c r="C4"><v>9.71502223333E+11</v></c><c r="D4" t="inlineStr"><is><t>omar@files.example</t></is></c></row>'
    '<row r="5"><c r="A5" t="inlineStr"><is><t>Huda Ali</t></is></c><c r="C5" t="inlineStr"><is><t>0507770000</t></is></c>'
    '<c r="E5" t="s"><v>9</v></c></row>'
)


def workbook(sheet: str = PEOPLE, *, strings: list[str] | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _RELS)
        archive.writestr("xl/sharedStrings.xml", _strings(_STRINGS if strings is None else strings))
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/worksheets/sheet2.xml", _sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>Never read</t></is></c></row>'))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    memory_data.clear()
    yield
    memory_data.clear()


def preview(body: dict[str, Any], hdrs: dict[str, str] = OWNER) -> Any:
    return client.post(f"{URL}/import/preview", headers=hdrs, json=body)


def test_an_excel_sheet_is_read_by_cell_reference_and_formulas_are_never_evaluated() -> None:
    body = {"format": "xlsx", "file_base64": b64(workbook()), "file_name": "people.xlsx"}
    response = preview(body)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["delimiter"] == "xlsx" and plan["columns"] == ["Name", "Company", "Mobile", "Email", "Do not contact"]
    assert plan["mapping"] == {"name": ["Name"], "organisation": "Company", "notes": None, "phones": ["Mobile"], "emails": ["Email"],
                               "do_not_contact": "Do not contact"}
    rows = {r["row"]: r for r in plan["rows"]}
    assert sorted(rows) == [2, 4, 5]  # the blank sheet row is no person; rows keep the sheet's numbers
    # A number stored as a number keeps every digit; the formula's last saved result is read as text.
    assert rows[2]["phones"] == ["+971501234567"] and rows[2]["emails"] == ["sara@files.example"] and rows[2]["organisation"] == "Tyre Co"
    # The company cell Excel left out does not shift the row: the email stays an email.
    assert rows[4]["organisation"] == "" and rows[4]["phones"] == ["+971502223333"] and rows[4]["emails"] == ["omar@files.example"]
    assert rows[5]["phones"] == ["+971507770000"] and rows[5]["do_not_contact"] == ["email"]
    assert plan["counts"]["new"] == 3
    # Apply with the same workbook writes all of it; the second sheet is never read.
    applied = client.post(f"{URL}/import/apply", headers=OWNER, json={**body, "mapping": plan["mapping"], "fingerprint": plan["fingerprint"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["created"] == 3 and applied.json()["marks_added"] == 1
    names = {p["name"] for p in client.get(URL, headers=OWNER).json()["items"]}
    assert names == {"Sara Ahmed", "Omar Khalid", "Huda Ali"}
    # The fingerprint covers the format: the same bytes previewed as Excel cannot be applied as another format.
    other = client.post(f"{URL}/import/apply", headers=OWNER, json={"format": "vcf", "csv": "x", "mapping": plan["mapping"],
                                                                      "fingerprint": plan["fingerprint"]})
    assert other.status_code == 409


def test_a_hostile_or_unreadable_workbook_is_refused_with_what_to_do() -> None:
    cases = [
        (workbook(extra={"xl/vbaProject.bin": b"macro"}), 422, "contains macros"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, 422, "old Excel file (.xls)"),
        (b"PK not really a zip", 422, "could not be read"),
        (workbook(_sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c></row>')), 422, "no people under it"),
        (workbook(_sheet("")), 422, "first sheet is empty"),
        (workbook(_sheet('<!DOCTYPE x [<!ENTITY e "boom">]><row r="1"><c r="A1"><v>1</v></c></row>')), 422, "could not be read"),
        (workbook(_sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="A1" t="inlineStr"><is><t>x</t></is></c>'
                         '<c r="BZ1" t="inlineStr"><is><t>Far</t></is></c></row>')), 422, "more than 60 columns"),
    ]
    for data, code, says in cases:
        response = preview({"format": "xlsx", "file_base64": b64(data)})
        assert response.status_code == code and says in response.json()["error"]["message"], (says, response.text)
    # A zip bomb: one part that expands far beyond its compressed size.
    bomb = workbook(extra={"xl/media/blank.bin": b"\x00" * (60 * 1024 * 1024)})
    response = preview({"format": "xlsx", "file_base64": b64(bomb)})
    assert response.status_code == 422 and "expands too much" in response.json()["error"]["message"]
    # Rows without a reference (or a broken one) are counted in order, never a server error.
    loose = workbook(_sheet('<row><c t="inlineStr"><is><t>Name</t></is></c><c t="inlineStr"><is><t>Phone</t></is></c></row>'
                            '<row r="x"><c t="inlineStr"><is><t>Rana</t></is></c><c t="inlineStr"><is><t>0507770000</t></is></c></row>'))
    loose_plan = preview({"format": "xlsx", "file_base64": b64(loose)}).json()
    assert [(r["row"], r["name"], r["phones"]) for r in loose_plan["rows"]] == [(2, "Rana", ["+971507770000"])]
    rows = "".join(f'<row r="{n}"><c r="A{n}" t="inlineStr"><is><t>P{n}</t></is></c></row>' for n in range(1, 2003))
    assert preview({"format": "xlsx", "file_base64": b64(workbook(_sheet(rows)))}).status_code == 413
    assert preview({"format": "xlsx", "file_base64": "not base64!"}).status_code == 422
    # The file goes in the field of its format, and only there.
    assert preview({"format": "xlsx", "csv": "Name\nA\n"}).status_code == 422
    assert preview({"format": "csv", "file_base64": b64(workbook())}).status_code == 422
    # Owners only, like a CSV file.
    assert preview({"format": "xlsx", "file_base64": b64(workbook())}, hdrs=MEMBER).status_code == 403


VCARDS = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sara Ahmed\r\nN:Ahmed;Sara;;;\r\nORG:Tyre Co;Fleet\r\n"
    "item1.TEL;TYPE=CELL:+971 50 123 4567\r\nTEL;TYPE=WORK:+971 4 555 0000\r\nEMAIL;TYPE=INTERNET:sara@files.example\r\n"
    "NOTE:Met at the fair\\, booth 4\\nCall after 3\r\nPHOTO;ENCODING=b;TYPE=JPEG:AAAA\r\n BBBB\r\nEND:VCARD\r\n"
    "BEGIN:VCARD\nVERSION:4.0\nN:Khalid;Omar;;Mr.;\nTEL;VALUE=uri:tel:+971502223333\nEMAIL:omar@files.example\nEND:VCARD\n"
    "BEGIN:VCARD\nVERSION:2.1\nFN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=D8=B1=D9=86=D8=A7 =D8=B5=D8=A7=D9=84=\n=D8=AD\n"
    "TEL;CELL:0507770000\nEND:VCARD\n"
    "BEGIN:VCARD\nVERSION:3.0\nFN:No Way To Reach\nEND:VCARD\n"
)


def test_a_vcard_file_is_one_row_per_card_with_every_number_and_address() -> None:
    body = {"format": "vcf", "csv": VCARDS, "file_name": "contacts.vcf"}
    response = preview(body)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["delimiter"] == "vcard" and plan["columns"] == ["Name", "Organisation", "Phone", "Email", "Notes"]
    assert plan["mapping"] == {"name": ["Name"], "organisation": "Organisation", "notes": "Notes", "phones": ["Phone"], "emails": ["Email"],
                               "do_not_contact": None}
    rows = {r["row"]: r for r in plan["rows"]}
    assert sorted(rows) == [2, 3, 4, 5]  # row 2 is the first card
    assert rows[2]["name"] == "Sara Ahmed" and rows[2]["organisation"] == "Tyre Co" and rows[2]["phones"] == ["+971501234567", "+97145550000"]
    assert rows[2]["emails"] == ["sara@files.example"]
    # vCard 4: the name from N (prefix, given, family) and a tel: URI read as the number.
    assert rows[3]["name"] == "Mr. Omar Khalid" and rows[3]["phones"] == ["+971502223333"]
    # vCard 2.1 quoted-printable, folded with a soft line break: an Arabic name.
    assert rows[4]["name"] == "رنا صالح" and rows[4]["phones"] == ["+971507770000"]
    assert rows[5]["name"] == "No Way To Reach" and rows[5]["status"] == "new"  # a name alone is enough, as in a CSV row
    applied = client.post(f"{URL}/import/apply", headers=OWNER, json={**body, "mapping": plan["mapping"], "fingerprint": plan["fingerprint"]})
    assert applied.status_code == 200, applied.text
    sara = next(p for p in client.get(URL, headers=OWNER).json()["items"] if p["name"] == "Sara Ahmed")
    detail = client.get(f"{URL}/{sara['id']}", headers=OWNER).json()["person"]
    assert detail["notes"] == "Met at the fair, booth 4\nCall after 3"


def test_a_broken_vcard_file_is_refused_with_what_to_do() -> None:
    for text, code, says in (
        ("BEGIN:VCARD\nFN:Open\n", 422, "not closed"),
        ("BEGIN:VCARD\nFN:A\nBEGIN:VCARD\nFN:B\nEND:VCARD\n", 422, "not closed"),
        ("Just some text\n", 422, "has no contacts"),
        ("BEGIN:VCARD\n\x00\nEND:VCARD\n", 422, "not a vCard text file"),
        ("BEGIN:VCARD\n" + "X-A:b\n" * 401 + "END:VCARD\n", 422, "too many fields"),
        ("BEGIN:VCARD\nNOTE:" + "a" * 20_001 + "\nEND:VCARD\n", 422, "too long"),
        ("".join(f"BEGIN:VCARD\nFN:P{n}\nTEL:+97150{n:07d}\nEND:VCARD\n" for n in range(2001)), 413, "at most 2000"),
    ):
        response = preview({"format": "vcf", "csv": text})
        assert response.status_code == code and says in response.json()["error"]["message"], (says, response.text)
