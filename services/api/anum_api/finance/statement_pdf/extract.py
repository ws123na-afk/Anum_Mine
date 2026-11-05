"""Layer 1: from PDF bytes to positioned words (docs/finance-statements.md#extraction).

* The file is checked and opened with pypdf (size and page caps, password in memory only). A
  wrong or missing password is a clear error, never a crash.
* Text PDFs: pdfminer.six (MIT, pure Python) gives every character's box. Characters are turned
  into the page's reading orientation (rotated pages and text drawn sideways are both handled by
  the text direction in each character's matrix), grouped into lines by their vertical middle and
  into words by the gaps between them; Arabic words are put back in reading order.
* Scanned PDFs (almost no text): the page images are taken out with pypdf for the OCR layer.

Blocking work: run it in a thread.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from .errors import PdfStatementError
from .words import ARABIC, Word, is_arabic, logical_order, normalize

MAX_PDF_BYTES = 10_000_000
MAX_PAGES = 60
MAX_OCR_PAGES = 20
MAX_PASSWORD = 128
MAX_WORDS_PER_PAGE = 20_000
# Under this many letters and digits on every page together, the PDF counts as a scan.
SCAN_THRESHOLD = 40

# pdfminer logs every token it parses at DEBUG, statement text included: never let it reach a log.
logging.getLogger("pdfminer").setLevel(logging.WARNING)

_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".tif": "image/tiff", ".tiff": "image/tiff",
                ".webp": "image/webp", ".gif": "image/gif"}


@dataclass
class PageImage:
    data: bytes
    content_type: str


@dataclass
class Extracted:
    page_count: int
    source: str  # "text" or "ocr"
    pages: list[list[Word]] = field(default_factory=list)
    widths: list[float] = field(default_factory=list)
    images: list[PageImage] = field(default_factory=list)
    sha256: str = ""


def _open(data: bytes, password: str | None) -> Any:
    from pypdf import PasswordType, PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        opened = PasswordType.NOT_DECRYPTED
        try:
            opened = reader.decrypt("")
            if opened == PasswordType.NOT_DECRYPTED and password:
                opened = reader.decrypt(password[:MAX_PASSWORD])
        except Exception:  # noqa: BLE001 - pypdf raises many types for a bad key or an unsupported cipher
            opened = PasswordType.NOT_DECRYPTED
        if opened == PasswordType.NOT_DECRYPTED:
            if password:
                raise PdfStatementError("password_wrong", "That password did not open the PDF. Check it and try again.")
            raise PdfStatementError("password_required", "This PDF is protected with a password. Type the password your bank gave you to open it.")
    return reader


def extract(data: bytes, password: str | None = None) -> Extracted:
    """Positioned words for every page, or the page images of a scan."""
    from pypdf.errors import PdfReadError

    if len(data) > MAX_PDF_BYTES:
        raise PdfStatementError("too_large", f"The PDF is larger than {MAX_PDF_BYTES // 1_000_000} MB. Download one month at a time.")
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise PdfStatementError("not_pdf", "This file is not a PDF. Pick the statement PDF your bank gave you.")
    sha = hashlib.sha256(data).hexdigest()
    try:
        reader = _open(data, password)
        page_count = len(reader.pages)
        if page_count == 0:
            raise PdfStatementError("damaged", "This PDF has no pages.")
        if page_count > MAX_PAGES:
            raise PdfStatementError("too_many_pages", f"This PDF has more than {MAX_PAGES} pages. Download one month at a time.")
        pages, widths = text_words(data, password)
        letters = sum(1 for page in pages for word in page for ch in word.text if ch.isalnum())
        if letters >= SCAN_THRESHOLD:
            return Extracted(page_count, "text", pages=pages, widths=widths, sha256=sha)
        if page_count > MAX_OCR_PAGES:
            raise PdfStatementError("too_many_pages", f"A scanned statement can have at most {MAX_OCR_PAGES} pages. Split it and import each part.")
        images = page_images(reader)
        if not images:
            raise PdfStatementError("no_text", "This PDF has no text and no page images, so there is nothing to read.")
        return Extracted(page_count, "ocr", images=images, sha256=sha)
    except PdfStatementError:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, IndexError, OSError, RecursionError, AttributeError, AssertionError) as exc:
        raise PdfStatementError("damaged", "This PDF is damaged or not a standard PDF. Download it again from your bank.") from exc
    except Exception as exc:  # noqa: BLE001 - pdfminer raises its own types (PSException and friends) for damaged files
        if type(exc).__module__.startswith(("pdfminer", "pypdf")):
            raise PdfStatementError("damaged", "This PDF is damaged or not a standard PDF. Download it again from your bank.") from exc
        raise


def page_images(reader: Any) -> list[PageImage]:
    images: list[PageImage] = []
    for page in reader.pages:
        found = [(len(image.data), image) for image in page.images]
        if not found:
            continue
        _, biggest = max(found, key=lambda item: item[0])
        suffix = "." + biggest.name.rsplit(".", 1)[-1].lower() if "." in biggest.name else ""
        content_type = _IMAGE_TYPES.get(suffix)
        payload = biggest.data
        if content_type is None:
            buffer = io.BytesIO()
            biggest.image.save(buffer, format="PNG")
            payload, content_type = buffer.getvalue(), "image/png"
        images.append(PageImage(payload, content_type))
    return images


# -- characters to words ------------------------------------------------------------------------


@dataclass
class _Char:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float


def _walk(item: Any) -> Any:
    from pdfminer.layout import LTChar

    if isinstance(item, LTChar):
        yield item
    elif hasattr(item, "__iter__"):
        for child in item:
            yield from _walk(child)


def _angle(matrix: tuple[float, ...]) -> int:
    """The text direction of a character in degrees, rounded to a quarter turn."""
    a, b = matrix[0], matrix[1]
    return int(round(math.degrees(math.atan2(b, a)) / 90.0)) % 4 * 90


def _upright(x: float, y: float, angle: int) -> tuple[float, float]:
    """A point turned so text drawn at ``angle`` reads left to right; returns (x, y-up)."""
    rad = math.radians(-angle)
    return x * math.cos(rad) - y * math.sin(rad), x * math.sin(rad) + y * math.cos(rad)


def text_words(data: bytes, password: str | None) -> tuple[list[list[Word]], list[float]]:
    from pdfminer.high_level import extract_pages

    pages: list[list[Word]] = []
    widths: list[float] = []
    for number, layout in enumerate(extract_pages(io.BytesIO(data), password=password or "", laparams=None, maxpages=MAX_PAGES), start=1):
        chars = list(_walk(layout))
        if not chars:
            pages.append([])
            widths.append(float(layout.width))
            continue
        # The page reads in the direction most of its characters are drawn in (a sideways statement).
        angle = Counter(_angle(c.matrix) for c in chars).most_common(1)[0][0]
        upright: list[_Char] = []
        for c in chars:
            if _angle(c.matrix) != angle:
                continue  # a watermark or a margin note drawn at another angle
            corners = [_upright(x, y, angle) for x, y in ((c.x0, c.y0), (c.x1, c.y0), (c.x0, c.y1), (c.x1, c.y1))]
            xs, ys = [p[0] for p in corners], [p[1] for p in corners]
            size = max(c.size, 0.1)
            upright.append(_Char(c.get_text(), min(xs), max(xs), -max(ys), -min(ys), size))
        xs = [c.x0 for c in upright] + [c.x1 for c in upright]
        left = min(xs) if xs else 0.0
        widths.append(float(layout.width if angle in (0, 180) else layout.height))
        for c in upright:
            c.x0 -= left
            c.x1 -= left
        pages.append(_words(upright, number)[:MAX_WORDS_PER_PAGE])
    return pages, widths


def _words(chars: list[_Char], page: int) -> list[Word]:
    """Characters grouped into lines by their vertical middle, then into words at spaces and gaps."""
    if not chars:
        return []
    height = median(c.bottom - c.top for c in chars) or 1.0
    chars.sort(key=lambda c: ((c.top + c.bottom) / 2, c.x0))
    lines: list[list[_Char]] = []
    for c in chars:
        middle = (c.top + c.bottom) / 2
        if lines and abs(middle - _middle(lines[-1])) <= height * 0.45:
            lines[-1].append(c)
        else:
            lines.append([c])
    words: list[Word] = []
    for line in lines:
        line.sort(key=lambda c: c.x0)
        current: list[_Char] = []
        for c in line:
            gap = c.x0 - current[-1].x1 if current else 0.0
            if c.text.isspace() or (current and gap > max(c.size, current[-1].size) * 0.22):
                if current:
                    words.append(_word(current, page))
                current = [] if c.text.isspace() else [c]
                continue
            current.append(c)
        if current:
            words.append(_word(current, page))
    return [w for w in words if w.text]


def _middle(line: list[_Char]) -> float:
    return sum((c.top + c.bottom) / 2 for c in line) / len(line)


def _word(chars: list[_Char], page: int) -> Word:
    text = normalize("".join(c.text for c in chars)).strip()
    if ARABIC.search(text) and is_arabic(text):
        text = logical_order(text)
    return Word(text, min(c.x0 for c in chars), max(c.x1 for c in chars), min(c.top for c in chars), max(c.bottom for c in chars), page)


__all__ = ["MAX_OCR_PAGES", "MAX_PAGES", "MAX_PASSWORD", "MAX_PDF_BYTES", "Extracted", "PageImage", "extract", "text_words"]
