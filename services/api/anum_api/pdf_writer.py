"""A small, dependency-free PDF writer: A4 pages with a title, paragraphs, label rows and tables.

Used by the tyre reports, the customer billing invoices and receipts, and the Business
invoices, receipts and credit notes. Latin text uses the built-in Helvetica fonts (WinAnsi);
Arabic is shaped, put in right-to-left order and drawn with the embedded Noto Sans Arabic
(``pdf_text.py``), so names, addresses and descriptions in Arabic print as Arabic, not "?".
The Arabic font is embedded only in a document that uses it. A character neither font covers
(Chinese, emoji) is drawn as "?" and never breaks the file. See docs/business.md#printing-and-pdf.

Bilingual documents: ``heading`` and ``table`` take an Arabic text next to the English one, and
``pairs`` draws label rows with the English label on the left, the value in the middle and the
Arabic label on the right, the layout Gulf invoices commonly use.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from typing import Any

from . import pdf_text
from .pdf_text import Run, layout, text_width

PAGE_WIDTH, PAGE_HEIGHT = 595.0, 842.0
MARGIN = 40.0

Column = str | tuple[str, str]


def _latin_literal(content: bytes) -> str:
    out = []
    for byte in content:
        if byte in (0x28, 0x29, 0x5C):
            out.append("\\" + chr(byte))
        elif 32 <= byte < 127:
            out.append(chr(byte))
        else:
            out.append(f"\\{byte:03o}")
    return "(" + "".join(out) + ")"


def fit(text: str, width: float, size: float, bold: bool = False) -> str:
    """``text`` cut (with "~") so it fits ``width`` points."""
    if text_width(text, size, bold) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if text_width(text[:middle] + "~", size, bold) <= width:
            low = middle
        else:
            high = middle - 1
    return (text[:low].rstrip() + "~") if low else "~"


def wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    """Lines of at most ``width`` points, broken between words (a too long word is cut)."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if text_width(candidate, size, bold) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = word if text_width(word, size, bold) <= width else fit(word, width, size, bold)
    if line:
        lines.append(line)
    return lines


class _Page:
    def __init__(self, document: "PdfDocument") -> None:
        self.ops: list[str] = []
        self.document = document

    def text(self, x: float, y: float, value: str, size: float = 9, bold: bool = False, *, align: str = "left", width: float | None = None,
             rtl: bool | None = None) -> None:
        runs, units = layout(value, bold, rtl)
        if not runs:
            return
        drawn = units * size / 1000.0
        if align == "right" and width is not None:
            x = x + width - drawn
        parts = [f"BT {x:.2f} {y:.2f} Td"]
        for run in runs:
            parts.append(self.document._show(run, size, bold))
        parts.append("ET")
        self.ops.append(" ".join(parts))

    def line(self, x1: float, y1: float, x2: float, y2: float, gray: float = 0.75) -> None:
        self.ops.append(f"{gray:.2f} G 0.5 w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S 0 G")

    def fill(self, x: float, y: float, w: float, h: float, gray: float = 0.93) -> None:
        self.ops.append(f"{gray:.2f} g {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f 0 g")


class PdfDocument:
    def __init__(self, footer: str) -> None:
        self.pages: list[_Page] = []
        self.footer = footer
        self.y = 0.0
        # Arabic glyphs used, per weight: glyph id -> the presentation form's code point.
        self._glyphs: dict[bool, dict[int, int]] = {False: {}, True: {}}
        self._new_page()

    # ------------------------------------------------------------------ fonts

    def _show(self, run: Run, size: float, bold: bool) -> str:
        if run.arabic:
            used = self._glyphs[bold]
            for glyph, code in zip(run.content, run.codes):  # type: ignore[arg-type]
                used.setdefault(int(glyph), code)
            hexed = "".join(f"{int(glyph):04X}" for glyph in run.content)  # type: ignore[union-attr]
            return f"/{'F4' if bold else 'F3'} {size:.1f} Tf <{hexed}> Tj"
        return f"/{'F2' if bold else 'F1'} {size:.1f} Tf {_latin_literal(run.content)} Tj"  # type: ignore[arg-type]

    @property
    def uses_arabic(self) -> bool:
        return bool(self._glyphs[False] or self._glyphs[True])

    # ------------------------------------------------------------------ layout

    def _new_page(self) -> None:
        self.pages.append(_Page(self))
        self.y = PAGE_HEIGHT - MARGIN

    @property
    def page(self) -> _Page:
        return self.pages[-1]

    def _need(self, height: float) -> None:
        if self.y - height < MARGIN + 20:
            self._new_page()

    def heading(self, text: str, size: float = 16, ar: str | None = None) -> None:
        """The title; with ``ar`` the Arabic title is set on the same line, on the right."""
        width = PAGE_WIDTH - 2 * MARGIN
        self._need(size + 8)
        self.y -= size
        if ar:
            half = width / 2 - 6
            self.page.text(MARGIN, self.y, fit(text, half, size, True), size, bold=True)
            self.page.text(MARGIN + width - half, self.y, fit(ar, half, size, True), size, bold=True, align="right", width=half, rtl=True)
        else:
            self.page.text(MARGIN, self.y, fit(text, width, size, True), size, bold=True,
                           align="right" if pdf_text.base_is_rtl(text) else "left", width=width)
        self.y -= 8

    def paragraph(self, text: str, size: float = 9, *, rtl: bool | None = None) -> None:
        """Wrapped text; a right-to-left paragraph (Arabic first) is set from the right margin."""
        width = PAGE_WIDTH - 2 * MARGIN
        direction = pdf_text.base_is_rtl(text) if rtl is None else rtl
        for item in wrap(text, width, size):
            self._need(size + 3)
            self.y -= size + 3
            self.page.text(MARGIN, self.y, item, size, align="right" if direction else "left", width=width, rtl=direction)
        self.y -= 4

    def pairs(self, rows: Sequence[tuple[str, str, Any]], size: float = 9) -> None:
        """Bilingual label rows: English label left, value, Arabic label right."""
        width = PAGE_WIDTH - 2 * MARGIN
        label_width = width * 0.27
        value_x = MARGIN + label_width + 6
        value_width = width - 2 * label_width - 12
        for english, arabic, value in rows:
            lines = wrap(_cell(value), value_width, size) or ["-"]
            for index, line in enumerate(lines):
                self._need(size + 4)
                self.y -= size + 4
                if index == 0:
                    self.page.text(MARGIN, self.y, fit(english, label_width, size, True), size, bold=True)
                    self.page.text(MARGIN + width - label_width, self.y, fit(arabic, label_width, size, True), size, bold=True,
                                   align="right", width=label_width, rtl=True)
                self.page.text(value_x, self.y, line, size)
            self.page.line(MARGIN, self.y - 3, MARGIN + width, self.y - 3, 0.9)
        self.y -= 6

    def table(self, title: str, columns: Sequence[Column], rows: Sequence[Sequence[Any]], *, title_ar: str | None = None) -> None:
        """A table; a column given as ``(english, arabic)`` gets a two-line header."""
        size = 8.0 if len(columns) <= 7 else 7.0
        width = PAGE_WIDTH - 2 * MARGIN
        texts = [[_cell(value) for value in row] for row in rows]
        english = [c[0] if isinstance(c, tuple) else c for c in columns]
        arabic = [c[1] if isinstance(c, tuple) else "" for c in columns]
        bilingual = any(arabic)
        weights = []
        for index, column in enumerate(english):
            longest = max([text_width(column, 1.0, True), text_width(arabic[index], 1.0, True)]
                          + [text_width(row[index], 1.0) for row in texts if index < len(row)])
            weights.append(min(max(longest, 2.2), 22.0))
        total = sum(weights) or 1
        widths = [width * weight / total for weight in weights]
        self._need(size * 4 + 20 + (size + 2 if bilingual else 0))
        self.y -= 12
        if title_ar:
            half = width / 2 - 6
            self.page.text(MARGIN, self.y, fit(title, half, 11, True), 11, bold=True)
            self.page.text(MARGIN + width - half, self.y, fit(title_ar, half, 11, True), 11, bold=True, align="right", width=half, rtl=True)
        else:
            self.page.text(MARGIN, self.y, fit(title, width, 11, True), 11, bold=True)
        self.y -= 6

        def header() -> None:
            height = (size + 6) + ((size + 2) if bilingual else 0)
            self.y -= height
            self.page.fill(MARGIN, self.y - 4, width, height + 1)
            x = MARGIN
            for index, column_width in enumerate(widths):
                top = self.y + ((size + 2) if bilingual else 0)
                self.page.text(x + 2, top, fit(english[index], column_width - 4, size, True), size, bold=True)
                if arabic[index]:
                    self.page.text(x + 2, self.y, fit(arabic[index], column_width - 4, size, True), size, bold=True,
                                   align="right", width=column_width - 4, rtl=True)
                x += column_width

        header()
        if not texts:
            self.y -= size + 5
            self.page.text(MARGIN + 2, self.y, "No rows.", size)
        for row in texts:
            if self.y - (size + 5) < MARGIN + 20:
                self._new_page()
                header()
            self.y -= size + 5
            x = MARGIN
            for value, column_width in zip(row, widths):
                rtl = pdf_text.base_is_rtl(value)
                self.page.text(x + 2, self.y, fit(value, column_width - 4, size), size, align="right" if rtl else "left", width=column_width - 4)
                x += column_width
            self.page.line(MARGIN, self.y - 2.5, MARGIN + width, self.y - 2.5, 0.88)
        self.y -= 8

    # ------------------------------------------------------------------ output

    def _font_objects(self, bold: bool, first_id: int) -> list[bytes]:
        """Type0 font, CID font, descriptor, font file and ToUnicode map (ids from ``first_id``)."""
        font = pdf_text.arabic_font(bold)
        used = sorted(self._glyphs[bold].items())
        name = f"ANUM{'B' if bold else 'R'}A+{font.name}"
        scale = 1000.0 / font.units_per_em
        widths = " ".join(f"{glyph} [{font.advance(glyph):.0f}]" for glyph, _ in used)
        bbox = " ".join(f"{value * scale:.0f}" for value in font.bbox)
        packed = zlib.compress(font.data, 9)
        cmap_lines = []
        for start in range(0, len(used), 100):
            chunk = used[start: start + 100]
            cmap_lines.append(f"{len(chunk)} beginbfchar")
            for glyph, code in chunk:
                text = pdf_text.to_unicode(code).encode("utf-16-be").hex().upper()
                cmap_lines.append(f"<{glyph:04X}> <{text}>")
            cmap_lines.append("endbfchar")
        cmap = (
            "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n" + "\n".join(cmap_lines)
            + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
        ).encode("ascii")
        type0, cid, descriptor, file_id, unicode_id = range(first_id, first_id + 5)
        return [
            (f"<< /Type /Font /Subtype /Type0 /BaseFont /{name} /Encoding /Identity-H "
             f"/DescendantFonts [{cid} 0 R] /ToUnicode {unicode_id} 0 R >>").encode(),
            (f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{name} "
             f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
             f"/FontDescriptor {descriptor} 0 R /CIDToGIDMap /Identity /DW 500 /W [{widths}] >>").encode(),
            (f"<< /Type /FontDescriptor /FontName /{name} /Flags 4 /FontBBox [{bbox}] /ItalicAngle 0 "
             f"/Ascent {font.ascent * scale:.0f} /Descent {font.descent * scale:.0f} /CapHeight {font.cap_height * scale:.0f} "
             f"/StemV {120 if bold else 80} /FontFile2 {file_id} 0 R >>").encode(),
            f"<< /Length {len(packed)} /Length1 {len(font.data)} /Filter /FlateDecode >>\nstream\n".encode() + packed + b"\nendstream",
            f"<< /Length {len(cmap)} >>\nstream\n".encode() + cmap + b"\nendstream",
        ]

    def render(self) -> bytes:
        # The footer first, so its glyphs count as used before the fonts are written.
        for number, page in enumerate(self.pages, start=1):
            page.text(MARGIN, MARGIN - 12, f"{self.footer}  -  page {number} of {len(self.pages)}", 7)
        objects: list[bytes] = [b"", b""]  # 1 catalog, 2 pages: filled below
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        fonts = "/F1 3 0 R /F2 4 0 R"
        for bold, key in ((False, "F3"), (True, "F4")):
            if self._glyphs[bold]:
                first = len(objects) + 1
                objects.extend(self._font_objects(bold, first))
                fonts += f" /{key} {first} 0 R"
        page_ids = []
        for page in self.pages:
            stream = "\n".join(page.ops).encode("ascii")
            page_id = len(objects) + 1
            page_ids.append(page_id)
            objects.append(
                (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] "
                 f"/Resources << /Font << {fonts} >> >> /Contents {page_id + 1} 0 R >>").encode()
            )
            objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode()
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for index, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if abs(value) < 1e9 else f"{value:.3g}"
    return str(value)
