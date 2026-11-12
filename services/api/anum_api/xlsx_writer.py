"""A small, dependency-free Excel (.xlsx) writer: sheets of rows, a bold header row, right-to-left sheets.

Used by the Vehicles monthly fleet report (docs/vehicles.md#monthly-fleet-report). Text is written as inline
strings and numbers as numbers; nothing is ever written as a formula, so a cell that starts with ``=``, ``+``,
``-`` or ``@`` stays text when the file is opened. Characters XML cannot carry (control characters) are dropped.
A sheet marked ``rtl`` opens right to left (Arabic). The file is a plain OOXML package (ZIP), the same parts Excel
writes for a workbook without styles beyond bold and number formats.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
MAX_SHEET_NAME = 31

# Styles: 0 plain, 1 bold (headers), 2 number with two decimals, 3 bold title.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>
<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


@dataclass
class Sheet:
    name: str
    rtl: bool = False
    rows: list[tuple[list[Any], str]] = field(default_factory=list)  # (cells, kind): kind is "title", "header" or "row"
    widths: list[float] = field(default_factory=list)

    def title(self, text: str) -> None:
        self.rows.append(([text], "title"))

    def header(self, cells: list[Any]) -> None:
        self.rows.append((list(cells), "header"))

    def row(self, cells: list[Any]) -> None:
        self.rows.append((list(cells), "row"))

    def blank(self) -> None:
        self.rows.append(([], "row"))


def escape(text: str, quotes: bool = False) -> str:
    """XML text (and, with ``quotes``, an attribute value): the five special characters written as entities."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;").replace("'", "&apos;") if quotes else text


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _clean(text: str) -> str:
    return _ILLEGAL.sub("", text)


def _cell(reference: str, value: Any, style: int) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number_style = 2 if style == 0 and isinstance(value, (float, Decimal)) else style
        return f'<c r="{reference}" s="{number_style}"><v>{value}</v></c>'
    text = escape(_clean(str(value)))
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{reference}" t="inlineStr" s="{style}"><is><t{space}>{text}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    widths = list(sheet.widths)
    lines = []
    for number, (cells, kind) in enumerate(sheet.rows, start=1):
        style = {"title": 3, "header": 1}.get(kind, 0)
        parts = [_cell(f"{_column(i)}{number}", value, style) for i, value in enumerate(cells)]
        for i, value in enumerate(cells):
            length = min(len(str(value)) if value is not None else 0, 60)
            if kind != "title":
                while len(widths) <= i:
                    widths.append(8.0)
                widths[i] = max(widths[i], length * 1.1 + 2)
        lines.append(f'<row r="{number}">{"".join(parts)}</row>')
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w:.1f}" customWidth="1"/>' for i, w in enumerate(widths))
    view = ' rightToLeft="1"' if sheet.rtl else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetViews><sheetView workbookViewId="0"{view}/></sheetViews>'
            + (f"<cols>{cols}</cols>" if cols else "")
            + f'<sheetData>{"".join(lines)}</sheetData></worksheet>')


def _sheet_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", _clean(name)).strip()[:MAX_SHEET_NAME] or "Sheet"
    candidate, counter = base, 2
    while candidate.casefold() in taken:
        suffix = f" {counter}"
        candidate = base[: MAX_SHEET_NAME - len(suffix)] + suffix
        counter += 1
    taken.add(candidate.casefold())
    return candidate


def workbook(sheets: list[Sheet]) -> bytes:
    """The .xlsx file's bytes."""
    if not sheets:
        raise ValueError("a workbook needs a sheet")
    taken: set[str] = set()
    names = [_sheet_name(sheet.name, taken) for sheet in sheets]
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                               'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
                     + "</Types>")
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                 'Target="xl/workbook.xml"/></Relationships>')
    book = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{escape(name, quotes=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, start=1))
            + "</sheets></workbook>")
    book_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                           f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
                 + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                   'Target="styles.xml"/></Relationships>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", book)
        archive.writestr("xl/_rels/workbook.xml.rels", book_rels)
        archive.writestr("xl/styles.xml", _STYLES)
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(sheet))
    return out.getvalue()


__all__ = ["Sheet", "workbook"]
