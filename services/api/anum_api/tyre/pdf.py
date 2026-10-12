"""A small, dependency-free PDF writer for tyre reports.

It lays out a title, a few highlight lines and tables on A4 pages with the built-in
Helvetica fonts. Built-in PDF fonts cover Latin text only (WinAnsi): characters outside
it, such as Arabic, are written as "?" (docs/tyre-planning.md#exports); the CSV export
keeps them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

PAGE_WIDTH, PAGE_HEIGHT = 595.0, 842.0
MARGIN = 40.0
# Average Helvetica glyph width as a fraction of the font size (good enough for layout).
CHAR_WIDTH = 0.52


def _ascii(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if 32 <= code < 127:
            out.append(char)
        elif code in (0x2013, 0x2014):
            out.append("-")
        elif code in (0x2018, 0x2019):
            out.append("'")
        elif code in (0x201C, 0x201D):
            out.append('"')
        elif code == 0xD7:
            out.append("x")
        else:
            out.append("?")
    return "".join(out)


def _escape(text: str) -> str:
    return _ascii(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fit(text: str, width: float, size: float) -> str:
    limit = max(1, int(width / (size * CHAR_WIDTH)))
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "~"


class _Page:
    def __init__(self) -> None:
        self.ops: list[str] = []

    def text(self, x: float, y: float, value: str, size: float = 9, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        self.ops.append(f"BT /{font} {size:.1f} Tf {x:.1f} {y:.1f} Td ({_escape(value)}) Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, gray: float = 0.75) -> None:
        self.ops.append(f"{gray:.2f} G 0.5 w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S 0 G")

    def fill(self, x: float, y: float, w: float, h: float, gray: float = 0.93) -> None:
        self.ops.append(f"{gray:.2f} g {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f 0 g")


class PdfDocument:
    def __init__(self, footer: str) -> None:
        self.pages: list[_Page] = []
        self.footer = footer
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append(_Page())
        self.y = PAGE_HEIGHT - MARGIN

    @property
    def page(self) -> _Page:
        return self.pages[-1]

    def _need(self, height: float) -> None:
        if self.y - height < MARGIN + 20:
            self._new_page()

    def heading(self, text: str, size: float = 16) -> None:
        self._need(size + 8)
        self.y -= size
        self.page.text(MARGIN, self.y, _fit(text, PAGE_WIDTH - 2 * MARGIN, size), size, bold=True)
        self.y -= 8

    def paragraph(self, text: str, size: float = 9) -> None:
        width = PAGE_WIDTH - 2 * MARGIN
        limit = max(10, int(width / (size * CHAR_WIDTH)))
        words, line = text.split(), ""
        lines = []
        for word in words:
            if len(line) + len(word) + 1 > limit:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            lines.append(line)
        for item in lines:
            self._need(size + 3)
            self.y -= size + 3
            self.page.text(MARGIN, self.y, item, size)
        self.y -= 4

    def table(self, title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        size = 8.0 if len(columns) <= 7 else 7.0
        width = PAGE_WIDTH - 2 * MARGIN
        texts = [[_cell(value) for value in row] for row in rows]
        weights = []
        for index, column in enumerate(columns):
            longest = max([len(column)] + [len(row[index]) for row in texts if index < len(row)])
            weights.append(min(max(longest, 4), 40))
        total = sum(weights) or 1
        widths = [width * weight / total for weight in weights]
        self._need(size * 4 + 20)
        self.y -= 12
        self.page.text(MARGIN, self.y, _fit(title, width, 11), 11, bold=True)
        self.y -= 6

        def header() -> None:
            self.y -= size + 6
            self.page.fill(MARGIN, self.y - 3, width, size + 6)
            x = MARGIN
            for column, column_width in zip(columns, widths):
                self.page.text(x + 2, self.y, _fit(column, column_width - 4, size), size, bold=True)
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
                self.page.text(x + 2, self.y, _fit(value, column_width - 4, size), size)
                x += column_width
            self.page.line(MARGIN, self.y - 2.5, MARGIN + width, self.y - 2.5, 0.88)
        self.y -= 8

    def render(self) -> bytes:
        objects: list[bytes] = []
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        page_ids = [5 + 2 * index for index in range(len(self.pages))]
        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode())
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        for number, page in enumerate(self.pages, start=1):
            page.text(MARGIN, MARGIN - 12, f"{self.footer}  -  page {number} of {len(self.pages)}", 7)
            stream = "\n".join(page.ops).encode("latin-1")
            content_id = 5 + 2 * (number - 1) + 1
            objects.append(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
                ).encode()
            )
            objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
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
