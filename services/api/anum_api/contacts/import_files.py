"""Excel workbooks and vCard files read as the import's table (docs/contacts.md#excel-and-vcard-files).

The CSV import (``importer.py``) works on a header and rows of text cells. These readers turn the two other
formats an owner is likely to have into the same table, so preview, mapping, checks, decisions and apply stay
exactly the CSV path:

* **Excel (.xlsx).** The workbook is hostile input. It is opened with the checked OOXML reader of
  ``doc_extract`` (``_Package``: entry count, total uncompressed size, compression ratio, encryption flag,
  bounded reads, XML through defusedxml with any ``DOCTYPE`` refused). Only the first sheet is read; its first
  non-empty row names the columns. A cell's stored value is taken as it is: a formula is never evaluated (its
  last saved result is read, as text), and a workbook that carries macros (``xl/vbaProject.bin``, an .xlsm
  saved as .xlsx) is refused, never opened further. An old binary workbook (.xls) or a password-protected
  one is refused with how to save it. Cells are placed by their reference (``C7``), so empty cells Excel
  leaves out do not shift a row. A whole number stored as a number (a phone number typed without ``+``) is
  written out in full, never in exponent form.
* **vCard (.vcf).** The text is read line by line with folded lines joined; each ``BEGIN:VCARD`` to
  ``END:VCARD`` is one person. ``FN`` (or ``N`` as given and family name), the first part of ``ORG``,
  ``NOTE``, every ``TEL`` and every ``EMAIL`` are kept, with vCard escapes undone and version 2.1
  quoted-printable values decoded; every other property (photos, keys, URLs, custom fields) is ignored. The
  table has the columns ``Name``, ``Organisation``, ``Phone``, ``Email`` and ``Notes``; several numbers or
  addresses are joined with `` | ``, which the CSV path splits again. Row 2 is the first card.

Both are capped like a CSV file: at most 2,000 people (413 past it), 60 columns, the file size limits below.
Errors are 422 with a sentence that says what to do.
"""

from __future__ import annotations

import base64
import binascii
import quopri
import re
from decimal import Decimal, InvalidOperation

from fastapi import status

MAX_WORKBOOK_BYTES = 2_000_000
MAX_VCARD_CHARS = 2_000_000
MAX_CARD_PROPERTIES = 400
MAX_LINE_CHARS = 20_000
VCARD_COLUMNS = ["Name", "Organisation", "Phone", "Email", "Notes"]
_CELL_REF = re.compile(r"^([A-Z]{1,3})([0-9]{1,7})$")
_NUMBER = re.compile(r"^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$")
_PROPERTY = re.compile(r"^(?:[A-Za-z0-9-]+\.)?([A-Za-z][A-Za-z0-9-]*)((?:;[^:]*)?):(.*)$", re.DOTALL)
# A table: the header, the rows (each with its record number, the header being 1) and how it was read.
Table = tuple[list[str], list[tuple[int, list[str]]], str]


def _error(code: int, detail: str) -> Exception:
    from .service import http_error

    return http_error(code, detail)


def _unprocessable(detail: str) -> Exception:
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


# ------------------------------------------------------------------------------------------------ Excel


def decode_workbook(encoded: str) -> bytes:
    """The workbook's bytes from the request's base64 text, size checked."""
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise _unprocessable("The Excel file did not arrive whole. Choose it again.") from None
    if not data:
        raise _unprocessable("That file is empty.")
    if len(data) > MAX_WORKBOOK_BYTES:
        raise _error(status.HTTP_413_CONTENT_TOO_LARGE, "The Excel file is larger than 2 MB. Split it, or save it as CSV, and choose it again.")
    return data


def _column_index(letters: str) -> int:
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - 64)
    return index - 1


def _plain_number(value: str) -> str:
    """A whole number written out in full (971501234567, not 9.71501234567E+11); anything else as stored."""
    if not _NUMBER.match(value):
        return value
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    if number == number.to_integral_value() and abs(number) < Decimal("1e20"):
        return str(number.to_integral_value().quantize(Decimal(1)))
    return value


def read_workbook(data: bytes, *, max_rows: int, max_columns: int) -> Table:
    """The first sheet of an .xlsx workbook as the import's table. Formulas are never evaluated."""
    from ..doc_extract import OLE_SIGNATURE, ExtractionError, ExtractionLimits, _Budget, _cell_value, _local, _Package, _relationship_id, _shared_strings

    if data.startswith(OLE_SIGNATURE):
        raise _unprocessable("This is an old Excel file (.xls) or a password-protected workbook. Open it in Excel and save it as .xlsx "
                             "(Excel Workbook) without a password, or as CSV, and choose it again.")
    limits = ExtractionLimits(max_input_bytes=MAX_WORKBOOK_BYTES, max_seconds=10.0, max_zip_entries=500, max_part_bytes=16 * 1024 * 1024,
                              max_uncompressed_bytes=48 * 1024 * 1024)
    try:
        package = _Package(data, limits)
        if "xl/workbook.xml" not in package.names:
            raise _unprocessable("This is not an Excel workbook (.xlsx). Save the sheet as .xlsx or CSV and choose it again.")
        if any(name.lower().startswith("xl/vbaproject") for name in package.names):
            raise _unprocessable("This workbook contains macros. ANUM never opens them: save it as an .xlsx workbook without macros, "
                                 "or as CSV, and choose it again.")
        budget = _Budget(limits)
        relationships = package.relationships("xl/workbook.xml")
        first = next((relationships.get(_relationship_id(element)) for _, element in package.events("xl/workbook.xml")
                      if _local(element.tag) == "sheet"), None)
        if not first:
            raise _unprocessable("The workbook has no sheet to read.")
        strings = _shared_strings(package, budget)
        header: list[str] | None = None
        header_number, last_number = 1, 0
        rows: list[tuple[int, list[str]]] = []
        for _, element in package.events(first):
            if _local(element.tag) != "row":
                continue
            budget.check()
            cells: dict[int, str] = {}
            position = 0
            for cell in element:
                if _local(cell.tag) != "c":
                    continue
                found = _CELL_REF.match(cell.get("r") or "")
                if found:
                    position = _column_index(found.group(1))
                if position >= max_columns:
                    raise _unprocessable(f"The sheet has more than {max_columns} columns; ANUM reads at most {max_columns}.")
                kind = cell.get("t")
                value = "" if kind == "e" else _cell_value(cell, strings)
                if kind in (None, "n"):
                    value = _plain_number(value)
                cells[position] = value
                position += 1
            written = element.get("r") or ""
            number = int(written) if written.isdigit() and len(written) <= 7 else last_number + 1
            last_number = number
            element.clear()
            width = max(cells) + 1 if cells else 0
            line = [cells.get(i, "") for i in range(width)]
            if not any(value.strip() for value in line):
                continue
            if header is None:
                header = line
                header_number = number
                continue
            rows.append((number - header_number + 1, line))
            if len(rows) > max_rows:
                raise _error(status.HTTP_413_CONTENT_TOO_LARGE, f"The sheet has more than {max_rows} people. Split it into files of at most {max_rows} rows.")
    except ExtractionError as exc:
        raise _unprocessable(f"The Excel file could not be read: {exc.message} Save it as .xlsx or CSV and choose it again.") from None
    if header is None:
        raise _unprocessable("The first sheet is empty. Its first row must name the columns (Name, Phone, Email...).")
    return header, rows, "xlsx"


# ------------------------------------------------------------------------------------------------ vCard


def _unescape(value: str) -> str:
    out: list[str] = []
    escaped = False
    for ch in value:
        if escaped:
            out.append("\n" if ch in "nN" else ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    return "".join(out)


def _components(value: str) -> list[str]:
    """A structured value (N, ORG) split on unescaped semicolons, each part unescaped."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in value:
        if escaped:
            current.append("\\" + ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ";":
            parts.append(_unescape("".join(current)))
            current = []
        else:
            current.append(ch)
    parts.append(_unescape("".join(current)))
    return parts


def _unfold(text: str) -> list[str]:
    """Physical lines with folded continuations joined (a line starting with a space or a tab continues the
    previous one; a version 2.1 quoted-printable value ending with ``=`` continues on the next line)."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif lines and lines[-1].endswith("=") and "QUOTED-PRINTABLE" in lines[-1].split(":", 1)[0].upper():
            lines[-1] = lines[-1][:-1] + raw
        else:
            lines.append(raw)
        if lines and len(lines[-1]) > MAX_LINE_CHARS:
            raise _unprocessable("A line of the vCard file is too long to be a contact. Export the contacts again and choose the file.")
    return lines


def _value(params: str, raw: str) -> str:
    upper = params.upper()
    if "QUOTED-PRINTABLE" in upper:
        charset = re.search(r"CHARSET=([A-Za-z0-9_-]{1,40})", params, re.IGNORECASE)
        try:
            return quopri.decodestring(raw.encode("latin-1", "replace")).decode(charset.group(1) if charset else "utf-8", "replace")
        except LookupError:
            return quopri.decodestring(raw.encode("latin-1", "replace")).decode("utf-8", "replace")
    return raw


def read_vcards(text: str, *, max_rows: int) -> Table:
    """Each card of a .vcf file as one row of Name, Organisation, Phone, Email and Notes."""
    text = text.lstrip("﻿")
    if "\x00" in text:
        raise _unprocessable("This is not a vCard text file. Export the contacts as vCard (.vcf) and choose the file again.")
    rows: list[tuple[int, list[str]]] = []
    card: dict[str, list[str]] | None = None
    properties = 0
    for line in _unfold(text):
        if not line.strip():
            continue
        found = _PROPERTY.match(line)
        if not found:
            continue  # not a property line: ignored, like any property ANUM does not read
        name, params, raw = found.group(1).upper(), found.group(2), found.group(3)
        if name == "BEGIN" and raw.strip().upper() == "VCARD":
            if card is not None:
                raise _unprocessable("A contact in the vCard file is not closed (END:VCARD is missing). Export the contacts again.")
            card, properties = {}, 0
            continue
        if card is None:
            continue
        if name == "END" and raw.strip().upper() == "VCARD":
            rows.append((len(rows) + 2, _row(card)))
            card = None
            if len(rows) > max_rows:
                raise _error(status.HTTP_413_CONTENT_TOO_LARGE, f"The file has more than {max_rows} contacts. Split it into files of at most {max_rows}.")
            continue
        properties += 1
        if properties > MAX_CARD_PROPERTIES:
            raise _unprocessable("A contact in the vCard file has too many fields to be read safely.")
        if name in ("FN", "N", "ORG", "NOTE", "TEL", "EMAIL"):
            card.setdefault(name, []).append(_value(params, raw))
    if card is not None:
        raise _unprocessable("The last contact in the vCard file is not closed (END:VCARD is missing). Export the contacts again.")
    if not rows:
        raise _unprocessable("The file has no contacts. Export them as vCard (.vcf), with BEGIN:VCARD and END:VCARD around each, and choose the file again.")
    return list(VCARD_COLUMNS), rows, "vcard"


def _clean(value: str) -> str:
    value = " ".join(value.replace("|", " ").split())
    lowered = value.casefold()
    for scheme in ("tel:", "mailto:"):  # vCard 4 writes numbers and addresses as URIs
        if lowered.startswith(scheme):
            return value[len(scheme):].strip()
    return value


def _row(card: dict[str, list[str]]) -> list[str]:
    name = _unescape(card.get("FN", [""])[0]).strip()
    if not name and card.get("N"):
        parts = _components(card["N"][0]) + ["", "", "", "", ""]
        family, given, middle, prefix, suffix = (p.strip() for p in parts[:5])
        name = " ".join(p for p in (prefix, given, middle, family, suffix) if p)
    organisation = _components(card["ORG"][0])[0] if card.get("ORG") else ""
    phones = [_clean(_unescape(v)) for v in card.get("TEL", [])]
    emails = [_clean(_unescape(v)) for v in card.get("EMAIL", [])]
    notes = "\n".join(_unescape(v).strip() for v in card.get("NOTE", []) if v.strip())
    return [" ".join(name.split()), " ".join(organisation.split()), " | ".join(p for p in phones if p), " | ".join(e for e in emails if e), notes]


__all__ = ["MAX_VCARD_CHARS", "MAX_WORKBOOK_BYTES", "VCARD_COLUMNS", "Table", "decode_workbook", "read_vcards", "read_workbook"]
