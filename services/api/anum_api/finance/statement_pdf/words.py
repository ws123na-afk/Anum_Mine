"""Words with their boxes, the unit every layer of the statement reader works on.

A word comes from a PDF's text layer (pdfminer.six character boxes, ``extract.py``) or from the OCR
service's word boxes (Tesseract TSV, ``ocr.py``). Coordinates are in the page's reading orientation:
``x`` grows to the right and ``top`` grows downwards, whatever the page rotation or text direction
was in the file. Text is normalized once here: Western digits, no control or direction characters,
Arabic presentation forms folded to letters, and Arabic words put in logical (reading) order.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_INVISIBLE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]")
ARABIC = re.compile("[\\u0600-\\u06ff\\u0750-\\u077f\\u08a0-\\u08ff\\ufb50-\\ufdff\\ufe70-\\ufeff]")


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int
    conf: float = 100.0  # 0 to 100; 100 for a PDF text layer
    # False for OCR lines that came without word boxes: no column can be read from their positions.
    positioned: bool = True

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.1)

    @property
    def middle(self) -> float:
        return (self.top + self.bottom) / 2


def normalize(text: str) -> str:
    """Western digits, no invisible or direction characters, presentation forms folded (NFKC)."""
    text = _INVISIBLE.sub(" ", text.translate(_DIGITS)).replace("−", "-")
    return unicodedata.normalize("NFKC", text).translate(_DIGITS)


def is_arabic(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and sum(1 for ch in letters if ARABIC.match(ch)) * 2 > len(letters)


def logical_order(visual: str) -> str:
    """An Arabic word drawn left to right (visual order) back in reading order. Runs of digits and
    Latin letters inside it keep their own order."""
    runs = re.findall(r"[0-9A-Za-z.,/:\-]+|[^0-9A-Za-z.,/:\-]+", visual)
    out = []
    for run in reversed(runs):
        out.append(run if re.match(r"[0-9A-Za-z.,/:\-]", run) else run[::-1])
    return "".join(out)


def reorder_rtl(words: list[str]) -> list[str]:
    """Words of a right-to-left line between visual (left to right on the page) and reading order.
    Runs of Arabic words reverse; runs of Latin words and numbers keep their own order, and the runs
    themselves reverse. Applying it twice gives the input back."""
    runs: list[list[str]] = []
    for word in words:
        rtl = bool(ARABIC.search(word))
        if runs and bool(ARABIC.search(runs[-1][0])) == rtl:
            runs[-1].append(word)
        else:
            runs.append([word])
    out: list[str] = []
    for run in reversed(runs):
        out.extend(reversed(run) if ARABIC.search(run[0]) else run)
    return out


__all__ = ["ARABIC", "Word", "is_arabic", "logical_order", "normalize", "reorder_rtl"]
