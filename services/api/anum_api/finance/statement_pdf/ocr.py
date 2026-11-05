"""Layer 3: scanned pages through the vault's OCR service (docs/finance-statements.md#scanned-pages).

The page images go to the existing OCR container (local Tesseract, English and Arabic). Its answer
carries each word's box, so a scan goes through the same geometry as a text PDF. An older OCR service
that sends lines without boxes still works: each line becomes words spaced in reading order, and the
reader falls back to its line rules. Nothing is kept: the lines live in this request only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .errors import PdfStatementError
from .extract import PageImage
from .words import Word, is_arabic, normalize, reorder_rtl

MAX_LINE = 600
OcrReader = Callable[[bytes, str, int], Awaitable[list[Word]]]


def words_from_ocr(lines: list[str], boxes: list[list[dict[str, float | str]]], confidences: list[float], page: int) -> list[Word]:
    """The OCR answer as words. With boxes, their pixel positions; without, one word per token, each
    line on its own row (no columns to read, so the line rules apply)."""
    words: list[Word] = []
    for index, text in enumerate(lines):
        line_boxes = boxes[index] if index < len(boxes) else []
        if line_boxes:
            for box in line_boxes:
                value = normalize(str(box.get("text", ""))).strip()
                if not value:
                    continue
                left, top = float(box.get("left", 0)), float(box.get("top", 0))
                width, height = float(box.get("width", 0)), float(box.get("height", 0))
                words.append(Word(value[:120], left, left + max(width, 1.0), top, top + max(height, 1.0), page, float(box.get("confidence", 0))))
            continue
        tokens = normalize(text[:MAX_LINE]).split()
        if tokens and is_arabic(" ".join(tokens)):
            tokens = reorder_rtl(tokens)  # placed right to left, as on the page
        conf = confidences[index] if index < len(confidences) else 50.0
        x = 0.0
        for token in tokens:
            # Two character widths between tokens: no column geometry can be read from it.
            words.append(Word(token, x, x + len(token), index * 2.0, index * 2.0 + 1.0, page, conf, positioned=False))
            x += len(token) + 2.0
    return words


async def ocr_page(data: bytes, content_type: str, page: int) -> list[Word]:
    """One scanned page's words from the vault's OCR service (the seam tests replace)."""
    from ...vault.ocr_client import read_text

    result = await read_text(data, content_type)
    return words_from_ocr([line.text for line in result.lines], result.boxes, [line.confidence for line in result.lines], page)


async def read_scanned(images: list[PageImage], reader: OcrReader | None = None) -> list[list[Word]]:
    """The words of each scanned page. Raises ``PdfStatementError("ocr_unavailable")`` with the reason."""
    from ...vault.ocr_client import OcrUnavailable

    read = reader or ocr_page
    pages: list[list[Word]] = []
    for number, image in enumerate(images, start=1):
        try:
            pages.append(await read(image.data, image.content_type, number))
        except OcrUnavailable as exc:
            raise PdfStatementError("ocr_unavailable", f"This PDF is a scan and could not be read: {exc}") from None
    return pages


__all__ = ["OcrReader", "ocr_page", "read_scanned", "words_from_ocr"]
